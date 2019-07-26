import pytest
import numpy as np
import numpy.testing as npt
import pandas as pd
import logging
from copy import deepcopy

from foehnix.foehnix import Control
from foehnix import families, Foehnix


# TODO add and test some nans in the ff data


def test_control_class():
    # family and switch are mandatory arguments
    with pytest.raises(TypeError):
        _ = Control('gaussian')

    with pytest.raises(TypeError):
        _ = Control()

    with pytest.raises(ValueError) as e:
        _ = Control('gaussian', 1)
    assert e.match('switch is mandatory and either True or False')

    with pytest.raises(ValueError) as e:
        _ = Control('gausdistribution', True)
    assert e.match('family must be a foehnix-family object or one of')

    # test for valid verbose argument
    with pytest.raises(ValueError) as e:
        _ = Control('gaussian', True, verbose='aus')
    assert e.match('Verbose must be one of True, False or')

    # test for maxiter
    with pytest.raises(ValueError) as e:
        _ = Control('logistic', True, maxit=1.2)
    assert e.match('maxit must be single integer or list of len 2')

    # test for tolerance
    with pytest.raises(ValueError) as e:
        _ = Control('gaussian', True, tol='Inf')
    assert e.match('tol must be single float or list of length 2')

    # left/right must be reasonable
    with pytest.raises(ValueError) as e:
        _ = Control('gaussian', True, left=10, right=-10)
    assert e.match('left must be smaller than right.')

    # left/right is not implemented at the moment
    with pytest.raises(NotImplementedError) as e:
        _ = Control('gaussian', True, left=-10, right=10)

    # finally make a good call an test the properties
    fc_basic = Control('gaussian', True)
    assert isinstance(fc_basic.family, families.Family)
    assert isinstance(fc_basic.family, families.GaussianFamily)
    assert np.isinf([fc_basic.left, fc_basic.right]).all()
    assert fc_basic.maxit_em == fc_basic.maxit_iwls == 100
    assert fc_basic.tol_em == fc_basic.tol_iwls == 1e-8
    assert fc_basic.standardize is True
    assert fc_basic.force_inflate is False

    fc_adv = Control('logistic', False, maxit=[42, 43],
                     tol=np.array([1e-5, 1e-6]), standardize=False)
    assert isinstance(fc_adv.family, families.LogisticFamily)
    assert fc_adv.maxit_em == 42
    assert fc_adv.maxit_iwls == 43
    assert fc_adv.tol_em == 1e-5
    assert fc_adv.tol_iwls == 1e-6
    assert fc_adv.standardize is False


def test_main_class_input(data, caplog):
    # first a basic initialization which should work
    mod = Foehnix('ff', data)
    # original dataframe should not be altered by Foehnix
    assert data.index[0] != mod.data.index[0]
    # assert attributes
    assert len(mod.concomitant) == 0
    assert mod.filter_method is None
    assert mod.predictor == 'ff'

    # test input for predictor and concomitant
    with pytest.raises(ValueError) as e:
        _ = Foehnix('foo', data)
    assert e.match('Predictor variable not found in data')

    with pytest.raises(ValueError) as e:
        _ = Foehnix('ff', data, concomitant=['rh', 'foo'])
    assert e.match('Concomitant "foo" not found in data')

    # lets test a strict filter
    # add some winddirection nans
    data.loc[data['dd'] <= 90, 'dd'] = np.nan
    with pytest.raises(RuntimeError) as e:
        _ = Foehnix('ff', data, filter_method={'dd': [0, 90]})
    assert e.match('No data left after applying required filters')

    # add a concomitant with constant values
    data['constant'] = 1
    with pytest.raises(RuntimeError) as e:
        _ = Foehnix('ff', data, concomitant='constant',
                    filter_method={'dd': [180, 360]})
    assert e.match('Columns with constant values in the data!')

    # check maxiteration settings, 1 and 0 should pass but raise a log warning
    caplog.set_level(logging.CRITICAL)

    mod = Foehnix('ff', data, maxit=1)
    assert ('The EM algorithm stopped after one iteration!' in
            caplog.records[-1].message)
    assert mod.optimizer['converged'] is False

    _ = Foehnix('ff', data, maxit=[0, 100])
    assert ('Iteration limit for the EM algorithm is turned off!' in
            caplog.records[-1].message)

    _ = Foehnix('ff', data, maxit=[100, 0])
    assert ('Iteration limit for the IWLS solver is turned off!' in
            caplog.records[-1].message)


def test_inflated_data(data, caplog):
    # add datarow inbetween
    data.loc[98.4, :] = {'ff': 1, 'dd': 1, 'rand': 1, 'rh': 1}
    data.index = (pd.to_datetime('1900-01-01') +
                  pd.to_timedelta(data.index, unit='d'))

    # test without sorting the index should fail
    with pytest.raises(RuntimeError) as e:
        _ = Foehnix('ff', data)
    assert e.match('DataFrame index is not monotonic increasing!')

    # sort index
    data.sort_index(inplace=True)

    # this should raise an error because inflation is to large
    with pytest.raises(RuntimeError):
        _ = Foehnix('ff', data)
    assert ('foehnix tries to inflate the time series' in
            caplog.records[-1].message)

    # this should work if we force it
    mod = Foehnix('ff', data, force_inflate=True)
    assert len(mod.data) > 2*len(data)
    # make a quite spcific test to assure the correct inflation
    assert mod.inflated == 147


def test_no_concomitant_fit(data):
    # simple but working model
    mod = Foehnix('ff', data, maxit=150)

    # 150 should be enough to converge
    assert mod.optimizer['converged']
    assert mod.optimizer['iter'] < 150

    # test some calculations
    npt.assert_equal(mod.optimizer['AIC'],
                     -2*mod.optimizer['loglik'] + 2*mod.optimizer['edf'])
    sum_1 = (mod.prob['flag'] == 1).sum()
    npt.assert_equal(mod.optimizer['BIC'],
                     -2*mod.optimizer['loglik'] + mod.optimizer['edf'] *
                     np.log(sum_1))

    # data is set up to have around 25% foehn probability
    mean_n = mod.prob.notna().sum()['flag']
    mean_occ = 100 * (mod.prob['prob'] >= .5).sum() / mean_n
    mean_prob = 100 * mod.prob['prob'][mod.prob['flag'].notna()].mean()
    npt.assert_almost_equal(mean_occ, 25, 0)
    npt.assert_almost_equal(mean_prob, 25, 0)

    # mean and standard deviation should match theta
    npt.assert_almost_equal(mod.optimizer['theta']['mu1'], 10, 0)
    npt.assert_almost_equal(mod.optimizer['theta']['mu2'], 25, 0)
    npt.assert_almost_equal(mod.optimizer['theta']['logsd1'], np.log(4), 0)
    npt.assert_almost_equal(mod.optimizer['theta']['logsd2'], np.log(7), 0)

    # with switch
    mod_switch = Foehnix('ff', data, switch=True)
