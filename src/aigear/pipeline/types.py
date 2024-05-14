class Step:
    def __init__(self, func, params, output_key: str = ""):
        self.func = func
        self.params = params
        self.output_key = output_key

    def __repr__(self):
        return f"Step{(self.func.__name__, self.params, self.output_key)}"


class Workflow:
    def __init__(self, *args):
        self.elements = []
        check_passed = self.__check_type(*args)
        if check_passed:
            self.elements = list(args)

    def append(self, step):
        self.elements.append(step)

    def __iter__(self):
        return iter(self.elements)

    def __repr__(self):
        return f"Workflow{tuple(self.elements)}"

    @staticmethod
    def __check_type(*args):
        check_passed = False
        for step in list(args):
            if isinstance(step, Step) or isinstance(step, Parallel) or isinstance(step, NBParallel):
                check_passed = True
            else:
                raise TypeError("Workflow only accepts Step and Parallel and NBParallel type from aigear.pipeline!")
        return check_passed


class Parallel:
    def __init__(self, *args):
        self.elements = list(args)

    def __iter__(self):
        return iter(self.elements)

    def __repr__(self):
        return f"Parallel{tuple(self.elements)}"


class NBParallel:
    def __init__(self, *args):
        self.elements = list(args)

    def __iter__(self):
        return iter(self.elements)

    def __repr__(self):
        return f"NBParallel{tuple(self.elements)}"
