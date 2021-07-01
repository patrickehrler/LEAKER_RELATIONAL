"""
For License information see the LICENSE file.

Authors: Amos Treiber

This file provides interfacing to various cardinality estimator implementations.

"""
import pandas as pd

from abc import abstractmethod, ABC
from typing import Any, Dict, Union

import torch

from ...api import RelationalDatabase, RelationalKeyword
from ...sql_interface import SQLRelationalDatabase
from ...extension import PandasExtension

from .naru.common import Table, CsvTable, TableDataset
from .naru.train_model import MakeMade, ReportModel, transformer, InitWeight, RunEpoch, Entropy, DEVICE
from .naru.estimators import ProgressiveSampling, CardEst


class RelationalEstimator(ABC):
    """Trains an estimator using the supplied dataset as a sample. The model is then used to provide cardinality
    estimates for queries."""

    _dataset_sample: SQLRelationalDatabase
    _estimator: Any

    def __init__(self, sample: RelationalDatabase):
        self._dataset_sample = sample
        self._estimator = None

    @abstractmethod
    def train(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def estimate(self, kw: RelationalKeyword) -> float:
        """Uses the trained model to estimate the selectivity/relative cardinality of kw on the full dataset
        based on the sample provided at training time."""
        raise NotImplementedError


class NaruRelationalEstimator(RelationalEstimator):
    """Uses the Naru estimator of Yang et al.: https://github.com/naru-project/naru"""

    _table_dict: Dict[int, Table]
    __epochs: int
    _estimator: Union[None, Dict[int, CardEst]] = None

    def __init__(self, sample: RelationalDatabase, epochs: int = 20):
        self._table_dict = dict()
        self.__epochs = epochs
        super().__init__(sample)

        if not self._dataset_sample.has_extension(PandasExtension):
            self._dataset_sample.extend_with(PandasExtension)
        pd_ext = self._dataset_sample.get_extension(PandasExtension)

        for table in self._dataset_sample.tables():
            table_id = self._dataset_sample.table_id(table)
            df = pd_ext.get_df(table_id)
            self._table_dict[table_id] = CsvTable(table, df, df.columns)

    def train(self) -> None:
        self._estimator = dict()
        for table_name in self._dataset_sample.tables():
            table_id = self._dataset_sample.table_id(table_name)

            table = self._table_dict[table_id]

            model = MakeMade(256, table.columns, None)
            ReportModel(model)

            if not isinstance(model, transformer.Transformer):
                model.apply(InitWeight)

            if isinstance(model, transformer.Transformer):
                opt = torch.optim.Adam(
                    list(model.parameters()),
                    2e-4,
                    betas=(0.9, 0.98),
                    eps=1e-9,
                )
            else:
                opt = torch.optim.Adam(list(model.parameters()), 2e-4)
            ReportModel(model)

            train_data = TableDataset(self._table_dict[table_id])

            table_bits = Entropy(
                table,
                table.data.fillna(value=0).groupby([c.name for c in table.columns
                                                    ]).size(), [2])[0]

            for epoch in range(self.__epochs):
                mean_epoch_train_loss = RunEpoch('train',
                                                 model,
                                                 opt,
                                                 train_data=train_data,
                                                 val_data=train_data,
                                                 batch_size=1024,
                                                 epoch_num=epoch,
                                                 table_bits=table_bits,
                                                 return_losses=True
                                                 )

            estimator = ProgressiveSampling(model, table, len(self._dataset_sample.row_ids(table_id)),
                                            device=torch.device(DEVICE))

            self._estimator[table_id] = estimator

            print(f"Done.")
            ReportModel(model)

    def estimate(self, kw: RelationalKeyword) -> float:
        if self._estimator is None:
            self.train()

        table = self._table_dict[kw.table]

        return self._estimator[kw.table].Query([c for c in table.Columns() if f"attr_{kw.attr}" in c.name], "=",
                                               [kw.value])
