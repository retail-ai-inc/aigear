import cloudpickle
import pickle


class PickleModel:
    def __init__(self):
        # Used to save sklearn models and other models
        self.framework = "pickle"

    @staticmethod
    def save(obj, file=None, protocol=None):
        if file is None:
            raise ValueError("The save path cannot be empty.")
        with open(file, "wb") as pickle_file:
            cloudpickle.dump(
                obj,
                pickle_file,
                protocol
            )

    @staticmethod
    def load(
        file,
        *,
        fix_imports=True,
        encoding="ASCII",
        errors="strict",
        buffers=None
    ):
        with open(file, "rb") as pickle_file:
            model = pickle.load(
                pickle_file,
                fix_imports=fix_imports,
                encoding=encoding,
                errors=errors,
                buffers=buffers,
            )
        return model
