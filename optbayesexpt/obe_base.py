__author__ = 'Bob McMichael'

import numpy as np

from optbayesexpt import GOT_NUMBA
from optbayesexpt import ParticlePDF

try:
    from scipy.stats import differential_entropy as diffent
except ImportError:
    from optbayesexpt.obe_utils import differential_entropy as diffent

if GOT_NUMBA:
    from numba import njit

rng = np.random.default_rng()
DEFAULT_N_DRAWS = 30


class OptBayesExpt(ParticlePDF):
    """An implementation of sequential Bayesian experiment design.

    OptBayesExpt is a manager that calculates strategies for efficient
    measurement runs. OptBayesExpt incorporates measurement data, and uses
    that information to select settings for measurements with high
    predicted benefit / cost ratios.

    The use cases are situations where the goal is to find the parameters of
    a parametric model.

    The primary functions of this class are to interpret measurement data
    and to calculate effective settings. The corresponding methods that
    perform these functions are ``OptBayesExpt.pdf_update()`` for
    interpretation of new data and either ``OptBayesExpt.opt_setting()`` or
    ``OptBayesExpt.good_setting()`` for calculation of effective settings.

    Instances of OptBayesExpt itself may be used for cases where

    #. Reported measurement data includes measurement uncertainty,
    #. Every measurement is assumed to cost the same amount.
    #. The measurement noise is assumed to be constant

    OptBayesExpt may be inherited by child classes to allow additional
    flexibility.  Examples in the ``demos`` folder show several extensions
    including unknown noise, and setting-dependent costs.

    Args:
        measurement_model (:obj:`function`): Evaluates the experimental model
            from (:code:`settings`, :code:`parameters`, :code:`constants`)
            arguments, returning single values or arrays depending on the
            arguments.  The :code:`model_function` is very similar to the
            fit function in a least-squares regression. The
            :code:`model_function()` must allow evaluation in both of the
            following forms:

            - :code:`model_function(tuple_of_single_settings,
              tuple_of_parameter_arrays, tuple_of_constants)`, returning an
              array with the same size as one of the parameter arrays.
            - :code:`model_function(tuple_of_setting_arrays,
              tuple_of_single_parameters, tuple_of_constants)`, returning
              an array with the same size as one of the setting arrays.

            The broadcasting feature of numpy arrays provides a convenient
            way to write this type of function for simple analytical models.

            Version 1.1.0 and later support model functions that return
            multiple output channels, e. g. real and imaginary parts or
            vectors expressed as tuples, lists or arrays. The number of
            output channels, ``n_channels`` is deduced by evaluating the
            measurement model function.

        setting_values (:obj:`tuple` of :obj:`ndarray`):
            Each array in the :code:`setting_values` tuple contains the
            allowed discrete values of a measurement setting.  Applied
            voltage, excitation frequency, and a knob that goes to eleven
            are all examples of settings. For computational speed,
            it is important to keep setting arrays appropriately sized.
            Settings arrays that cover unused setting values, or that use
            overly fine discretization will slow the calculations. Settings
            that are held constant belong in the :code:`constants` array.

        parameter_samples (:obj:`tuple` of :obj:`ndarray`):
            Each array in the :code:`parameter_samples` tuple contains the
            possible values of a model parameter.  In a simple example
            model, :code:`y = m * x + b`, the parameters are :code:`m` and
            :code:`b`.  As with the :code:`setting_values`,
            :code:`parameter_samples` arrays should be kept few and small.
            Parameters that can be assumed constant belong in the
            :code:`constants` array. Discretization should only be fine
            enough to support the needed measurement precision. The
            parameter ranges must also be limited: too broad, and the
            computation will be slow; too narrow, and the measurement may
            have to be adjusted and repeated.

        constants (:obj:`tuple` of :obj:`float`):
            Model constants.  Examples include experimental settings that
            are rarely changed, and model parameters that are well-known
            from previous measurement results.

        n_draws (:obj:`int`): specifies the number of parameter samples used
            in the utility calculation.  Default 30.

        choke (:obj:`float`): If ``choke`` is specified, the likelihood will be
            raised to the ``choke`` power. Occasionally, simulated
            measurement runs will "get stuck," and converge to incorrect
            parameter values. The ``choke`` argument provides a heuristic
            fix for better reliability at the expense of speed.  For values
            ``0.0 < choke < 1.0`` choking reduces the max/min ratio of the
            likelihood and allows more data to influence the parameter
            distribution between resampling events. Default ``None``.

        use_jit (:obj:`Boolean`): If ``numba`` is installed, pre-compile the
            likelihood calculation for faster execution.  Arg ``use_jit`` is also
            passed as a keyword arg to ParticlPDF. Default ``True``.

        utility_method (:obj:`string`)
            [``'variance_approx'`` | ``'pseudo_utility'`` |
            ``'full_kld_utility'`` | ``'max_min'``]:  Specifies the utility
            algorithm as described in [#f1]_. Default ``'variance_approx'``.

        selection_method (:obj:`string`)
            [``'optimal'`` | ``'good'`` | ``'random'``]:
            Specifies how the setting is selected based on the
            utility. If ``'optimal'``, the setting at maximum utility is
            selected. If ``'good'``, the utility is raised to a power given
            by ``pickiness`` parameter and normalized. The setting is
            selected with probability proportional to ``utility`` **
            ``pickiness``.  If ``'random``, the utility is disregarded and
            the setting is chosen randomly from the allowed settings.

        pickiness (:obj:`float`):
            When `selection_method` is ``'normal'``,
            this parameter affects the probability of picking a setting near a
            maximum in the utilty function. Default 15.

        default_noise_std (:obj:`float` -or- ``ndarray``):
            Measurement noise standard deviation used in utility
            calculations.  If ``float``, the value populates entries of a
            :math:`n_{channels} \\times 1` ``ndarray`` where :math:`n_{
            channels}` corresponds to the number of measurement channels,
            e.g. 2 if data is collected from :math:`X` and :math:`Y` outputs
            of an instrument. If  :math:`n_{channels} \\times 1` ``ndarray``,
            entries are noise standard deviations corresponding to the
            measurement channels. For cases where the measurement noise
            depends on settings, the

        \*\*kwargs: Keyword arguments passed to the parent ParticlePDF class.

    Attributes:

        model_function (:obj:`function`): Same as the ``model_function``
            parameter above.

        setting_values (:obj:`tuple` of :obj:`ndarray`): A record of the
            setting_values argument.

        allsettings (:obj:`list` of :obj:`ndarray`): Arrays containing all
            possible combinations of the setting values provided in the
            ``setting_values`` argument.

        setting_indices (:obj:`ndarray` of :obj:`int`): indices in to
            the allsettings arrays. Used in ``opt_setting()`` and
            ``good_setting()``.

        parameters (:obj:`ndarray` of :obj:`ndarray`): The most recently
            set of parameter samples the parameter distribution.
            ``self.parameters`` is a *view* of ``PartcilePDF.particles``.

        cons (:obj:`tuple` of :obj:`float`): Stores the ``constants``
            argument tuple.

        default_noise_std (:obj:`float` | :obj:`): value copied from
            ``default_noise_std`` setting.

        measurement_results (:obj:`list`): A list containing records of
            accumulated measurement results.

        last_setting_index (:obj:`int`): The most recent settings
            recommendation as an index into ``self.allsettings``.

        N_DRAWS (int): The number of parameter draws to use in the utility
            calculation to estimate the variance of model outputs due to
            parameter distribution.  Default: 30
    """

    def __init__(self, measurement_model, setting_values, parameter_samples,
                 constants, n_draws=DEFAULT_N_DRAWS, choke=None,
                 use_jit=True, utility_method='variance_approx',
                 selection_method='optimal', pickiness=15,
                 default_noise_std=1.0,
                 **kwargs):
        """ Initializes an OptBayesExpt object.

        and here's more stuff for the document.

        """
        self.model_function = measurement_model
        self.setting_values = setting_values
        self.allsettings = np.array([s.flatten() for s in
                                     np.meshgrid(*setting_values,
                                                 indexing='ij')])
        self.setting_indices = np.arange(len(self.allsettings[0]))
        ParticlePDF.__init__(self, parameter_samples, use_jit=use_jit,
                             **kwargs)

        self.parameters = self.particles
        self.cons = constants
        self.choke = choke
        self.N_DRAWS = n_draws
        self.pickiness = pickiness

        # A list containing records of accumulated measurement results
        self.measurement_results = []
        # Indices of most recent requested setting
        self.last_setting_index = 0
        # The number of parameter draws to use in the utility calculation.
        # Default: 30

        # Test the supplied model
        # n_channels = values per measurement = model output values
        self.n_channels = self._model_output_len()

        # In order to handle single-channel and multi-channel measurements
        # the same way, make single-channel model outputs iterable over
        # channels.
        if self.n_channels == 1:
            def wrapped_function(s, p, c):
                y = measurement_model(s, p, c)
                return (y,)

            self._model_function = wrapped_function
        else:
            self._model_function = self.model_function

        self.utility_y_space = np.array([])
        self.set_n_draws(n_draws)
        # A noise level estimate used in setting selection
        # used by ``y_var_noise_model()``.
        self.default_noise_std = np.ones((self.n_channels, 1)) * \
                                 default_noise_std

        utilitymethods = ['variance_approx', 'pseudo_utility',
                               'full_kld_utility', 'max_min']
        if utility_method == 'variance_approx':
            self.utility = self.utility_variance
        elif utility_method == 'pseudo_utility':
            self.utility = self.utility_pseudo
        elif utility_method == 'max_min':
            self.utility = self.utility_max_min
        elif utility_method == 'full_kld_utility':
            self.utility = self.utility_full_kld
        else:
            raise SyntaxError(f'Unknown utility method: {utility_method}. '
                              f'Valid utility methods are{utilitymethods}')

        self.set_selection_method(selection_method)

        # pre-compile some numeric routines using numba.njit
        if GOT_NUMBA and use_jit:
            # create a just-in-time compiled helper routine to do the
            # numerical
            # heavy lifting
            @njit(cache=True, nogil=True)
            def _gauss_noise_likelihood(y_model, y_meas, sigma):
                return np.exp(
                    -((y_model - y_meas) / (sigma + 1e-16)) ** 2 / 2) / (sigma + 1e-16)
        else:
            # No numba package installed?  No problem.
            def _gauss_noise_likelihood(y_model, y_meas, sigma):
                return np.exp(
                    -((y_model - y_meas) / (sigma + 1e-16)) ** 2 / 2) / (sigma + 1e-16)
        self._gauss_noise_likelihood = _gauss_noise_likelihood

    def set_selection_method(self, selection_method):
        selection_methods = ['optimal', 'good', 'random']
        self.selection_method = selection_method
        if selection_method == 'optimal':
            self.get_setting = self.opt_setting
        elif selection_method == 'good':
            self.get_setting = self.good_setting
        elif selection_method == 'random':
            self.get_setting = self.random_setting
        else:
            raise SyntaxError(f'Unknown selection_method: {selection_method}.'
                              f'Valid selection methods are'
                              f' {selection_methods}')

    def set_n_draws(self, n_draws=None):
        """Sets OptBayesExpt.N_DRAWS attribute.

        Sets or queries the number of parameter samples to use in the utility
        calculation.

        Args:
            n_draws (int or 'default' or None):  An
                integer argument sets N_DRAWS, 'default' sets the default value
                of 30, and ``set_n_draws()`` returns the current value.

        Returns: N_DRAWS
        """
        if n_draws == 'default':
            # reset to the default value
            self.N_DRAWS = DEFAULT_N_DRAWS
        elif n_draws:
            # non-zero or not None
            self.N_DRAWS = n_draws
        self.utility_y_space = np.zeros((self.N_DRAWS,
                                         self.n_channels,
                                         len(self.allsettings[0])))
        return self.N_DRAWS

    def eval_over_all_parameters(self, onesettingset):
        """Evaluates the experimental model.

        Evaluates the model for one combination of measurement settings and
        all parameter combinations in ``self.parameters``. Called by
        ``pdf_update()`` for ``likelihood()`` and Bayesian inference
        processing of measurement results.

        This method and ``eval_over_all_settings()`` both call
        ``model_function()``, but with different argument types.  If the
        broadcasting properties of numpy arrays are not able to resolve this
        polymorphism, this method may be replaced by a separate method for
        model evaluation.

        Args:
            onesettingset (:obj:`tuple` of :obj:`float`): a single set of
                measurement settings

        Returns:
            (:obj:`ndarray`) array of model values with dimensions of one
            element of :obj:`self.allparams`.
        """
        return self._model_function(onesettingset, self.parameters, self.cons)

    def eval_over_all_settings(self, oneparamset):
        """Evaluates the experimental model.

        Evaluates the model for all combinations of measurement settings in
        ``self.allsettings`` and one set of parameters. Called ``N_DRAWS``
        times by ``yvar_from_parameter_draws()`` as part of the ``utility()``
        calculation

        Args:
            oneparamset (:obj:`tuple` of :obj:`float`): a single set of
                model parameters.

        Returns:
            (:obj:`ndarray`) array of model values with dimensions of one
            element of :code:`self.allsettings`.
        """
        return self._model_function(self.allsettings, oneparamset, self.cons)

    def pdf_update(self, measurement_record, y_model_data=None):
        """
        Refines the parameters' probability distribution function given a
        measurement result.

        This is the measurement result input method. An implementation of
        Bayesian inference, uses the model to calculate the ikelihood of
        obtaining the measurement result :code:`ymeas` as a function of
        parameter values, and uses that likelihood to generate a refined
        *posterior* ( after-measurement) distribution from the *prior* (
        pre-measurement) distribution.

        Args:
            measurement_record (:obj:`tuple`): A record of the measurement
                containing at least the settings and the measured value(s).
                The first element of ``measurement_record`` gets passed as a
                settings tuple to ``evaluate_over_all_parameters()`` The
                entire ``measurement_result`` tuple gets forwarded to
                ``likelihood()``.

            y_model_data (:obj:`ndarray`): The result of
                :code:`self.eval_over_all_parameters()` This argument allows
                model evaluation to run before measurement data is
                available, e.g. while measurements are being made. Default =
                ``None``.
        """
        # unpack the measurement result
        onesetting = measurement_record[0]

        # calculate the model for all values of the parameters
        if y_model_data is None:
            y_model_data = self.eval_over_all_parameters(onesetting)

        # Calculate the *likelihood* of measuring `measurmennt_result` for
        # all parameter combinations
        # Product of likelihoods from the different channels
        likyhd = self.likelihood(y_model_data, measurement_record)

        # update the pdf using a method inherited from ParticlePDF()

        self.bayesian_update(likyhd)
        self.parameters = self.particles
        if self.just_resampled:
            self.enforce_parameter_constraints()

        return self.particles, self.particle_weights

    def enforce_parameter_constraints(self):
        """A stub for enforcing constraints on parameters

        for example::

            # find the particles with disallowed parameter values
            # (negative parameter values in this example)
            bad_ones = np.argwhere(self.parameters[3] < 0)
                for index in bad_ones:
                    # setting a weight = 0 effectively eliminates the particle
                    self.particle_weights[index] = 0
            # renormalize
            self.particle_weights = self.particle_weights / np.sum(self.particle_weights)

        """
        pass

    def likelihood(self, y_model, measurement_record):
        """
        Calculates the likelihood of a measurement result.

        For each parameter combination, estimate the probability of
        obtaining the results provided in :code:`measurement_result`.  This
        default method relies on several assumptions:

        - A single measurement yields a single value :math:`y_{meas}`
        - The uncertainty in that value is well-described by additive
          Gaussian noise.
        - The the standard deviation of the noise, :math:`\sigma` is known.

        Under these assumptions, and model values :math:`y_{model}` as a
        function of parameters, the likelihood is a Gaussian function
        proportional to :math:`\sigma^{-1} \exp [-(y_{model} - y_{meas})^2
        / (2 \sigma^2)]`.

        Args:
            y_model (:obj:`ndarray`): ``model_function()`` results evaluated
                for all parameters.
            measurement_record (:obj:`tuple`): The measurement conditions
                and results, supplied by the user to ``update_pdf()``. The
                elements of ``measurement_record`` are:

                    - settings (tuple)
                    - measurement value (float)
                    - std uncertainty (float)

        Returns:
            an array of probabilities corresponding to the parameters in
            :code:`self.allparameters`.
        """
        # unpack the measurement_record
        onesetting, y_meas, sigma = measurement_record
        lky = 1.0
        for y_m, y, s in zip(y_model,
                             np.atleast_1d(y_meas),
                             np.atleast_1d(sigma)):
            lky *= self._gauss_noise_likelihood(y_m, y, s)

        if self.choke is not None:
            return np.power(lky, self.choke)
        else:
            return lky

    def yvar_from_parameter_draws(self):
        """Models the measurement variance solely due to parameter
        distributions.

        Evaluates the effect of the distribution
        of parameter values on the distribution of model outputs for every
        setting combination. This calculation is done as part of the
        *utility* calculation as an approximation to the information
        entropy. For each of ``self.N_DRAWS`` samples from the parameter
        distribution, this method models a noise-free experimental output
        for all setting combinations and returns the variance of the model
        values for each setting combination.

        Returns:
            :obj:`ndarray` with shape of a member of all_settings
        """

        paramsets = self.randdraw(self.N_DRAWS).T

        # fill the model results for each drawn parameter set
        for i, oneparamset in enumerate(paramsets):
            self.utility_y_space[i] = self.eval_over_all_settings(oneparamset)

        # Evaluate how much the model varies at each setting
        # calculate the variance of results for each setting
        yvar = np.var(self.utility_y_space, axis=0)
        return yvar

    def yvar_from_entropy(self):
        """Models the entropy of the model values due to the
        parameter distributions

        Evaluates the effect of the distribution
        of parameter values on the distribution of model outputs for every
        setting combination. This calculation is done as part of the
        *utility* calculation as an approximation to the information
        entropy. For each of ``self.N_DRAWS`` samples from the parameter
        distribution, this method models a noise-free experimental output
        for all setting combinations and returns the entropy of the model
        values for each setting combination, cast as a variance

        Returns:
            :obj:`ndarray` with shape of a member of all_settings
        """

        paramsets = self.randdraw(self.N_DRAWS).T

        # fill the model results for each drawn parameter set
        for i, oneparamset in enumerate(paramsets):
            self.utility_y_space[i] = self.eval_over_all_settings(oneparamset)

        # Evaluate how much the model varies at each setting
        # calculate the variance of results for each setting
        model_entropy = diffent(self.utility_y_space, axis=0)
        yvar = np.exp(2 * model_entropy) / (2 * np.pi * np.e)
        return yvar

    def yvar_max_min(self):
        """
        Crudely approximates the signal variance using max - min.

        Returns: :obj:`ndarray` with shape of a member of all_settings
        """
        paramsets = self.randdraw(self.N_DRAWS).T

        # fill the model results for each drawn parameter set
        for i, oneparamset in enumerate(paramsets):
            self.utility_y_space[i] = self.eval_over_all_settings(oneparamset)

        span = np.max(self.utility_y_space, axis=0) - \
               np.min(self.utility_y_space, axis=0)

        return span ** 2

    def y_var_noise_model(self):
        # for backawards compatibiilty. Stupid typo.
        return self.yvar_noise_model()

    def yvar_noise_model(self):
        """
        A stub for models of the measurement noise

        A model of measurement variance (noise) as a function of settings,
        averaged over parameters if parameter-dependent.  Used in the
        *utility* calculation.

        In general, the measurement noise could depend on both settings and
        parameters, and the model would require evaluation of the noise
        model over all parameters, averaged over draws from the parameter
        distribution.  Measurement noise that depends on the measurement
        value, like root(N), Poisson-like counting noise is an example of
        such a situation. Fortunately, this noise estimate only affects the
        utility function, which only affects setting choices, where the
        "runs good" philosophy of the project allows a little approximation.

        Returns:
            If measurement noise is independent of settings, a :obj:`float`,
            otherwise an :obj:`ndarray` with the shape of an
            element of `allsettings`.  Default: ``default_noise_std ** 2``.
        """
        return self.default_noise_std ** 2

    def cost_estimate(self):
        """ A stub for estimating the cost of prospective measurements

        The denominator of the *utility* function allows measurement
        resources (e.g. setup time + data collection time) to be entered
        into the utility calculation.

        Returns:
            :obj:`float`, otherwise an :obj:`ndarray` with
            the dimensions of one array in ``allsettings`` describing how the
            variance of measurement noise depends on settings.  Default: 1.0.
        """
        return 1.0

    def utility_max_min(self):
        """ Estimate the utility as a function of settings.

        The *utility* is the predicted benefit/cost ratio of a new
        measurement where the benefit is given in terms of a change in the
        information entropy of the parameter distribution. This algorithm
        corresponds to the "max-min algorithm" of [#f1]_.

        In this algorithm, we use the maximum and minimum modeled
        outputs produced by :code:`N_DRAWS` samples of the parameter
        distribution and the variance of the measurement noise are calculated
        separately.

        This algorithm provides slightly lower quality setting choices than
        the other utility algorithms, but it executes very fast. Speed and
        quality of choices are both best when ``N_DRAWS = 2``.

        Returns:
            Approximate utility as an :obj:`ndarray` with dimensions of a
            member of :code:`allsettings`.
        """
        var_p = self.yvar_max_min()
        var_n = self.yvar_noise_model()
        cost = self.cost_estimate()
        utility = np.sum(np.log(1 + var_p / var_n), axis=0)
        return utility / cost

    def utility_variance(self):
        """ Estimate the utility as a function of settings.

        The *utility* is the predicted benefit/cost ratio of a new
        measurement where the benefit is given in terms of a change in the
        information entropy of the parameter distribution. This algorithm
        corresponds to the "variance algorithm" of [#f1]_.

        In this algorithm, we use the logarithm of variance as an
        approximation for the information entropy.  The variance of model
        outputs produced by :code:`N_DRAWS` samples of the parameter
        distribution and the variance of the measurement noise are calculated
        separately.

        Execution of ``utility_variance`` is faster than ``utility_variance``
        and ``utility_pseudo`` and the decision quality is very similar to
        ``utility_KLD``.

        Returns:
            Approximate utility as an :obj:`ndarray` with dimensions of a
            member of :code:`allsettings`.
        """
        var_p = self.yvar_from_parameter_draws()
        var_n = self.yvar_noise_model()
        cost = self.cost_estimate()
        utility = np.sum(np.log(1 + var_p / var_n), axis=0)
        return utility / cost

    def utility_pseudo(self):
        """
        Estimate the utility as a function of settings.

        Used in selecting measurement settings. The *utility* is the
        predicted benefit/cost ratio of a new measurement where the benefit
        is given in terms of a change in the information entropy of the
        parameter distribution. This algorithm
        corresponds to the "pseudo-H algorithm" of [#f1]_, and it is included
        here mostly for historical reasons.

        In this algorithm, the idea is to mimic the :code:`utility_KLD()`
        algorithm more closely than ``utility_variance()``. We calculate the
        differential entropy of the model outputs produced by
        :code:`N_DRAWS` samples
        of the parameter distribution. We then compute the variance of a
        normal (Gaussian) distribution that has the same information entropy.
        This effective variance is combined with the noise variance as in
        ``utility_variance()``.

        Returns:
            Approximate utility as an :obj:`ndarray` with dimensions of a
            member of :code:`allsettings`.
        """
        var_p = self.yvar_from_entropy()
        var_n = self.yvar_noise_model()
        cost = self.cost_estimate()
        utility = np.sum(np.log(1 + var_p / var_n), axis=0)
        return utility / cost

    def reset_proposed_setting(self, ):
        self.proposed_settings = {
            "setting_val": [],
            "setting_idx": [],
            "setting_bin": np.zeros(self.setting_indices.shape)
        }

    def save_proposed_settings(self, setting, setting_idx):
        if not hasattr(self, 'proposed_settings'):
            self.reset_proposed_setting()
        self.proposed_settings["setting_val"].append(setting)
        self.proposed_settings["setting_idx"].append(setting_idx)
        self.proposed_settings["setting_bin"][setting_idx] += 1

    def utility_full_kld(self):
        """
        Estimate the utility as a function of settings.

        Used in selecting measurement settings. The *utility* is the
        predicted benefit/cost ratio of a new measurement where the benefit
        is given in terms of a change in the information entropy of the
        parameter distribution. This algorithm
        corresponds to the "full-KLD algorithm" of [#f1]_.

        Among the provided utility algorithms, ``utility_KLD`` comes closest to
        the information-theoretic analytical result.

        Returns:
            Approximate utility as an :obj:`ndarray` with dimensions of a
            member of :code:`allsettings`.
        """

        # randomly draw parameter samples
        paramsets = self.randdraw(self.N_DRAWS).T
        # model measurement noise
        nva = rng.normal(0, 1.0, self.N_DRAWS * self.n_channels)
        nvb = nva.reshape((self.n_channels, self.N_DRAWS))
        noisevalues = (nvb * np.sqrt(self.yvar_noise_model())).T
        # fill the model results for each drawn parameter set
        for i, oneparamset in enumerate(paramsets):
            self.utility_y_space[i] = self.eval_over_all_settings(oneparamset) \
                                      + noisevalues[i]

        y_entropy = diffent(self.utility_y_space, axis=0)
        n_entropy = diffent(noisevalues, axis=0)
        return y_entropy - n_entropy


    def opt_setting(self):
        """Find the setting with maximum predicted impact on the parameter
        distribution.

        At what settings are we most uncertain about how an experiment will
        come out? That is where the next measurement will do the most good.
        So, we calculate model outputs for a bunch of possible model
        parameters and see where the output varies the most. We use our
        accumulated knowledge by drawing the possible parameters from the
        current parameter pdf.

        Returns:
            A settings tuple.
        """

        utility = self.utility()
        self.utility_stored = utility.copy()
        # Find the settings with the maximum utility
        # argmax returns an array of indices into the flattened array
        bestindex = np.argmax(utility)

        # translate to setting values
        # allsettings is a list of setting arrays generated by np.meshgrid,
        # one for each 'knob'
        bestvalues = self.allsettings[:, bestindex]

        self.last_setting_index = bestindex
        self.save_proposed_settings(tuple(bestvalues), bestindex)
        return tuple(bestvalues)

    def good_setting(self, pickiness=None):
        """
        Calculate a setting with a good probability of refining the pdf

        ``good_setting()`` selects settings using a weighted random
        selection using the utility function to calculate a weight.  The
        weight function is ``utility( )`` raised to the ``pickiness`` power.
        In comparison to the ``opt_setting()`` method, where the
        measurements select only the very best setting, ``good_setting()``
        yields a more diverse series of settings.

        Args:
            pickiness (float): A setting selection tuning parameter.
                Pickiness=0 produces random settingss.  With pickiness
                values greater than about 10 the behavior is similar to
                :code:`opt_setting()`.

        Returns:
            A settings tuple.
        """
        if pickiness is None:
            pickiness = self.pickiness

        utility = (self.utility()) ** pickiness
        self.utility_stored = utility.copy()
        # the exponent 'pickiness' is a tuning parameter

        utility /= np.sum(utility)
        goodindex = self.rng.choice(self.setting_indices, p=utility)
        goodvalues = self.allsettings[:, goodindex]

        self.last_setting_index = goodindex
        self.save_proposed_settings(tuple(goodvalues), goodindex)
        return tuple(goodvalues)

    def random_setting(self):
        """
        Pick a random setting for the next measurement

        ``random_setting()`` randomly selects a setting from all possible
        setting combinations.

        Returns:
            A settings tuple.
        """
        randindex = rng.choice(self.setting_indices)
        randvalues = self.allsettings[:, randindex]
        self.save_proposed_settings(tuple(randvalues), randindex)
        return tuple(randvalues)

    def _model_output_len(self):
        # """Detect the number of model outputs
        #
        # :return: int
        # """

        try:
            rng = np.random.default_rng()
        except AttributeError:
            rng = np.random

        settingindex = rng.choice(self.setting_indices)
        one_setting = self.allsettings[:, settingindex]
        one_param_set = self.randdraw(n_draws=1)

        singleshot = self.model_function(one_setting, one_param_set, self.cons)

        return len(np.atleast_1d(singleshot))
