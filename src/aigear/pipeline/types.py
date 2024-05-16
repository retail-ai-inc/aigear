class RelyParams:
    def __init__(self, rely_output_key: str = "", rely_index=()):
        """
        Used to set dependent input parameters.

        rely_output: The output key of the dependency function.
        rely_index: The index of the dependent function output.
            lIt is used to find the output result as a parameter.
        """
        if rely_output_key == "":
            raise TypeError("rely_output_key is mandatory.")
        if not isinstance(rely_output_key, str):
            raise TypeError("rely_output_key is string.")
        self.rely_output_key = rely_output_key

        if isinstance(rely_index, tuple) or isinstance(rely_index, list) or isinstance(rely_index, dict):
            self.rely_index = rely_index
        else:
            raise TypeError("Set position parameters for list or tuple, and set keyword parameters for dict.")

    def __repr__(self):
        return f"RelyParams({self.rely_output_key}, {self.rely_index})"


class InputParams(list):
    def __init__(self, *args):
        """
        In order to standardize the format of input parameters.
        """
        if self._check_type(args):
            super().__init__(args)
        else:
            raise TypeError("Cannot have both positional and keyword parameters simultaneously.")

    def __repr__(self):
        return f"InputParams{tuple(element for element in self)}"

    def _check_type(self, args):
        is_dict = [isinstance(element.rely_index, dict) if isinstance(element, RelyParams)
                   else isinstance(element, dict) for element in args]
        self.is_dict = all(is_dict)
        return all(is_dict) or not any(is_dict)


class Step:
    def __init__(self, func, params: InputParams, output_key: str = ""):
        self.func = func
        self.params = params

        # If output_key is not set, the output result will be saved using the function name
        if output_key == "":
            self.output_key = func.__name__
        else:
            self.output_key = output_key

    def __repr__(self):
        return f"Step{(self.func.__name__, self.params, self.output_key)}"


class Workflow:
    def __init__(self, *args):
        self.elements = []
        check_passed = self._check_type(*args)
        if check_passed:
            self.elements = list(args)

    def append(self, step):
        self.elements.append(step)

    def __iter__(self):
        return iter(self.elements)

    def __repr__(self):
        return f"Workflow{tuple(self.elements)}"

    @staticmethod
    def _check_type(*args):
        check_passed = False
        for step in list(args):
            if isinstance(step, Step) or isinstance(step, Parallel) or isinstance(step, NBParallel):
                check_passed = True
            else:
                raise TypeError("Workflow only accepts Step and Parallel and NBParallel type from aigear.pipeline.")
        return check_passed


class Parallel:
    def __init__(self, *args):
        """
        Using multithreading to parallelize IO intensive tasks
        """
        self.elements = list(args)

    def __iter__(self):
        return iter(self.elements)

    def __repr__(self):
        return f"Parallel{tuple(self.elements)}"


class NBParallel:
    def __init__(self, *args):
        """
        Due to global locking, using multiple processes to parallel CPU intensive tasks to prevent blocking.
        """
        self.elements = list(args)

    def __iter__(self):
        return iter(self.elements)

    def __repr__(self):
        return f"NBParallel{tuple(self.elements)}"
