import logging
import statistics
import sys

import numpy as np
from typing import Tuple, List, Union

import pandas
import seaborn as sns
import matplotlib.pyplot as plt

from leaker.api import RelationalDatabase, RelationalQuery
from leaker.attack.relational_estimators.estimator import NaruRelationalEstimator, KDERelationalEstimator, \
    SamplingRelationalEstimator, RelationalEstimator
from leaker.extension import IdentityExtension, CoOccurrenceExtension
from leaker.sql_interface import SQLBackend

# SPECIFY TO BE USED DATASET HERE
dataset = 'dmv_full'

f = logging.Formatter(fmt='{asctime} {levelname:8.8} {process} --- [{threadName:12.12}] {name:32.32}: {message}',
                      style='{')
console = logging.StreamHandler(sys.stdout)
console.setFormatter(f)
file = logging.FileHandler('eval_' + dataset + '.log', 'w', 'utf-8')
file.setFormatter(f)
logging.basicConfig(handlers=[console, file], level=logging.DEBUG)
log = logging.getLogger(__name__)

# Import
backend = SQLBackend()
log.info(f"has dbs {backend.data_sets()}")
mimic_db: RelationalDatabase = backend.load(dataset)
log.info(
    f"Loaded {mimic_db.name()} data. {len(mimic_db)} documents with {len(mimic_db.keywords())} words. {mimic_db.has_extension(IdentityExtension)}")


def evaluate_actual_rlen(keywords):
    mimic_db.open()
    rlen_list = []
    for q in keywords:
        rlen_list.append(sum(1 for _ in mimic_db(q)))

    median = statistics.median(rlen_list)
    point95 = statistics.quantiles(data=rlen_list, n=100)[94]
    point99 = statistics.quantiles(data=rlen_list, n=100)[98]
    maximum = max(rlen_list)

    mimic_db.close()
    return median, point95, point99, maximum


def ErrorMetric(est_card, card):
    if card == 0 and est_card != 0:
        return est_card
    if card != 0 and est_card == 0:
        return card
    if card == 0 and est_card == 0:
        return 1.0
    return max(est_card / card, card / est_card)


def evaluate_estimator(estimator: RelationalEstimator, keywords: Union[List[RelationalQuery], List[Tuple[RelationalQuery, RelationalQuery]]],
                       use_cooc=False, ignore_zero_one=False) -> Tuple[float, float, float, float]:
    """
    Run list of (co-occ) queries on estimator and calculate statistic properties.

    Other Parameters
    ----------------
    estimator : RelationalEstimator
        actual estimator
    keywords : Union[List[RelationalQuery], List[Tuple[RelationalQuery, RelationalQuery]]]
        list or queries or list of query tuples (in case of cooc)
    use_cooc : bool
        calculate selectivity based on two keywords
    ignore_zero_one : bool
        ignore cases where estimation is zero and true selectivity is one
    """
    error_value_list = []
    if not use_cooc:
        _full_identity = mimic_db.get_extension(IdentityExtension)
        for q in keywords:
            est_value = estimator.estimate(q)
            actual_value = len(_full_identity.doc_ids(q))
            if not (ignore_zero_one and est_value == 0 and actual_value == 1):
                current_error = ErrorMetric(est_value, actual_value)
                error_value_list.append(current_error)
    else:
        _full_coocc = mimic_db.get_extension(CoOccurrenceExtension)
        for q1, q2 in keywords:
            est_value = estimator.estimate(q1, q2)
            actual_value = _full_coocc.co_occurrence(q1, q2)
            if not (ignore_zero_one and est_value == 0 and actual_value == 1):
                current_error = ErrorMetric(est_value, actual_value)
                error_value_list.append(current_error)

    median = np.median(error_value_list)
    point95 = np.quantile(error_value_list, 0.95)
    point99 = np.quantile(error_value_list, 0.99)
    maximum = np.max(error_value_list)

    '''
    log.info('MEDIAN: ' + str(median))
    log.info('.95: ' + str(point95))
    log.info('.99: ' + str(point99))
    log.info('MAX: ' + str(maximum))
    '''
    return median, point95, point99, maximum


def run_rlen_eval(nr_of_evals=1, nr_of_queries=100, use_cooc=False, ignore_zero_one=False):
    if use_cooc:
        if not mimic_db.has_extension(CoOccurrenceExtension):
            mimic_db.extend_with(CoOccurrenceExtension)
    else:
        if not mimic_db.has_extension(IdentityExtension):
            mimic_db.extend_with(IdentityExtension)

    results_list = []
    for i in range(0, nr_of_evals):
        log.info('######################################')
        log.info('RUNNING ITERATION: ' + str(i+1))
        log.info('######################################')
        for known_data_rate in [i / 10 for i in range(2, 10, 2)]:
            mimic_db.open()
            sampled_db = mimic_db.sample(known_data_rate)

            if not use_cooc:
                kw_sample = mimic_db.queries(max_queries=nr_of_queries)
            else:
                # sample two lists of random queries, then combine them to co-occurrence
                kw_sample1 = mimic_db.queries(max_queries=nr_of_queries)
                kw_sample2 = mimic_db.queries(max_queries=nr_of_queries)
                kw_sample = list(zip(kw_sample1, kw_sample2))

            # SAMPLING
            sampling_est = SamplingRelationalEstimator(sample=sampled_db, full=mimic_db)
            log.info('SAMPLING')
            results_list.append(
                ('SAMPLING-' + str(known_data_rate),) + evaluate_estimator(sampling_est, kw_sample, use_cooc, ignore_zero_one))

            # NAIVE
            # naive_est = NaiveRelationalEstimator(sample=sampled_db, full=mimic_db)
            # log.info('NAIVE')
            # results_list.append(('NAIVE-' + str(known_data_rate),) + evaluate_estimator(naive_est, kw_sample, use_cooc, ignore_zero_one))

            # KDE Estimator
            kde_est = KDERelationalEstimator(sample=sampled_db, full=mimic_db)
            log.info('KDE')
            results_list.append(('KDE-' + str(known_data_rate),) + evaluate_estimator(kde_est, kw_sample, use_cooc, ignore_zero_one))

            # UAE Estimator
            # uae_est = UaeRelationalEstimator(sample=sampled_db, full=mimic_db, nr_train_queries=200)
            # 20000 training queries in UAE paper
            # log.info('UAE')
            # results_list.append(
            #    ('UAE-' + str(known_data_rate) + '-200',) + evaluate_estimator(uae_est, kw_sample, use_cooc, ignore_zero_one))

            # Neurocard Estimator
            # neurocard_est = NeurocardRelationalEstimator(sample=sampled_db, full=mimic_db)
            # log.info('Neurocard')
            # results_list.append(
            #    ('Neurocard-' + str(known_data_rate),) + evaluate_estimator(neurocard_est, kw_sample, use_cooc, ignore_zero_one))

            # NARU Estimator
            naru_est = NaruRelationalEstimator(sample=sampled_db, full=mimic_db)
            log.info('NARU')
            results_list.append(('NARU-' + str(known_data_rate),) + evaluate_estimator(naru_est, kw_sample, use_cooc, ignore_zero_one))

    df = pandas.DataFrame(data=results_list, columns=['method', 'median', '.95', '.99', 'max'])
    df = df.groupby(['method']).mean()
    df = df.sort_index()
    sns_plot = sns.heatmap(df, annot=True, cmap='RdYlGn_r', fmt=".1f")
    plt.title('dataset=' + str(mimic_db.name()) + ', cooc=' + str(use_cooc) + ', nr_of_evals=' + str(nr_of_evals) + ', nr_of_queries=' + str(nr_of_queries) +
              ', ignore_zero_one=' + str(ignore_zero_one))
    sns_plot.figure.savefig("estimators.png", bbox_inches='tight')
    mimic_db.close()


run_rlen_eval(nr_of_evals=5, nr_of_queries=1000, use_cooc=False, ignore_zero_one=False)
# print(evaluate_actual_rlen(mimic_db.keywords()))
