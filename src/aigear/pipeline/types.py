class RelyParams:
    def __init__(self, rely_output_key, /, *args):
        """
        Used to set dependent input parameters.

        step: Dependent steps.
        *args: Follow the key to find the output of the dependent step. If not set, all will be returned.
        """
        # if not isinstance(step, Step):
        #     raise TypeError("The step is Step and mandatory.")
        # self.rely_output_key = step.output_key.key
        self.rely_output_key = rely_output_key

        if self._check_type(args):
            self.keywords = tuple(args)
        else:
            raise TypeError("Keywords is a string type.")

    def __repr__(self):
        if self.keywords:
            return f"RelyParams({self.rely_output_key}, {self.keywords})"
        else:
            return f"RelyParams({self.rely_output_key})"

    @staticmethod
    def _check_type(args):
        is_str = [isinstance(element, str) for element in args]
        return all(is_str)


class InputParams(list):
    def __init__(self, *args):
        """
        In order to standardize the format of input parameters.
        """
        super().__init__(args)

    def __repr__(self):
        return f"InputParams{tuple(element for element in self)}"


class OutputKey:
    def __init__(self, key, /, *args):
        """
        Used to set output.

        key: the key of output.
        *args: Follow the key to find the output of the dependent step. If not set, all will be returned.
        """
        if not isinstance(key, str):
            raise TypeError("The key is Step and mandatory.")
        self.key = key

        if self._check_type(args):
            self.keywords = tuple(args)
        else:
            raise TypeError("Keywords is a string type.")

    def __repr__(self):
        if self.keywords:
            return f"OutputKey({self.key}, {self.keywords})"
        else:
            return f"OutputKey({self.key})"

    @staticmethod
    def _check_type(args):
        is_str = [isinstance(element, str) for element in args]
        return all(is_str)


class Step:
    def __init__(self, function, params=None, output_key=None):
        self.func = function
        self.params = self._input_params(params)
        self.output_key = self._output_key(output_key)

    def __repr__(self):
        return f"Step(Function({self.func.__name__}), {self.params}, {self.output_key})"

    @staticmethod
    def _input_params(params):
        if params is None:
            return InputParams()
        elif isinstance(params, InputParams):
            return params
        elif isinstance(params, tuple) or isinstance(params, list):
            return InputParams(*params)
        else:
            return InputParams(params)

    def _output_key(self, output_key):
        # If output is not set, the output result will be saved using the function name
        if output_key is None:
            return OutputKey(self.func.__name__)
        elif isinstance(output_key, OutputKey):
            return output_key
        elif isinstance(output_key, str):
            return OutputKey(output_key)
        elif isinstance(output_key, tuple) or isinstance(output_key, list):
            if all([isinstance(element, str) for element in output_key]):
                return OutputKey(self.func.__name__, *output_key)
            else:
                raise TypeError("The Keys is a string type in output_key.")
        else:
            raise TypeError("Set one or more keywords to accept function output.")

    def get_output(self, *args):
        if not all([isinstance(element, str) for element in args]):
            raise TypeError("The keywords is a string type.")
        return RelyParams(self.output_key.key, *args)


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
