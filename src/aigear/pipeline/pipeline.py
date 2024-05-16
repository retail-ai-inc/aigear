from .types import Step, Parallel, NBParallel, Workflow
from .executor import step_executor, thread_executor, process_executor


class Pipeline:
    def __init__(
            self,
            name: str = "",
            version: str = "",
            describe: str = "",
    ):
        """
        Used to manage pipeline related functions and information

        name: pipeline name
        version: pipeline version to manage process flow
        """
        self.workflows = None
        self.name = name
        self.version = version
        self.describe = describe
        self.all_output_results = {}

    @staticmethod
    def step(func, parm, output_key: str = ""):
        """
        Used to define the relationships between tasks
        """
        if output_key == "":
            output_key = func.__name__
        step = Step(func, parm, output_key)
        return step

    def workflow(self, *args):
        """
        Used to define how to run
        """
        self.workflows = Workflow(*args)
        return self.workflows

    def run(self):
        result = self._run(self.workflows)
        return result

    def stop(self):
        pass

    def _run(self, workflow):
        for step in workflow:
            if isinstance(step, Step):
                self.all_output_results[step.output_key] = step_executor(step, self.all_output_results)
            elif isinstance(step, Workflow):
                self._run(step)
            elif isinstance(step, Parallel):
                results_dict = thread_executor(step, self.all_output_results)
                self.all_output_results.update(results_dict)
            elif isinstance(step, NBParallel):
                results_dict = process_executor(step, self.all_output_results)
                self.all_output_results.update(results_dict)
            else:
                raise TypeError("Workflow only accepts Step and Parallel and NBParallel type from aigear.pipeline!")
        return self.all_output_results
