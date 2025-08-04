from abc import ABC, abstractmethod


class Constraints(ABC):
    def pickle_save(
        self,
        model: object,
        model_name: str,
        version: str = None
    ):
        pass

    def pickle_load(
        self,
        model_name: str,
        version: str = None
    ):
        pass

    def list(
        self,
        model_name: str = None
    ):
        pass

    def delete(
        self,
        model_name: str,
        version: str,
    ):
        pass

    def path(
        self,
        model_name: str,
        version: str = None,
    ):
        pass
