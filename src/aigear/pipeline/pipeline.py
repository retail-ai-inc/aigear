import asyncio
from aigear.pipeline.types import Step, Parallel, NBParallel, Workflow


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
        self.name = name
        self.version = version
        self.describe = describe
        self.steps = []
        self.func_outputs = {}
        self.workflows = None
        self.parallels = None
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
        result = asyncio.run(self._sequence_run(self.workflows))
        return result

    async def _sequence_run(self, workflow):
        for step in workflow:
            if isinstance(step, Step):
                await self._run_step(step)
            elif isinstance(step, Workflow):
                await self._sequence_run(step)
            elif isinstance(step, Parallel) or isinstance(step, NBParallel):
                await asyncio.gather(
                    *(self._run_step(sub_step) if isinstance(sub_step, Step) else self._sequence_run(sub_step) for
                      sub_step in step))
            else:
                raise TypeError("Workflow only accepts Step and Parallel and NBParallel type from aigear.pipeline!")
        return self.all_output_results

    async def _run_step(self, step):
        func = step.func
        params = step.params
        output_key = step.output_key

        # If output_key is not set, the output result will be saved using the function name
        if output_key == "":
            output_key = func.__name__

        # Resolve all parameters
        all_params = []
        if isinstance(params, tuple):
            for parm in params:
                if isinstance(parm, dict):
                    params_list = self._expand_rely_params(parm)
                    all_params.extend(params_list)
                else:
                    all_params.append(parm)
        elif isinstance(params, dict):
            params_list = self._expand_rely_params(params)
            all_params.extend(params_list)
        else:
            all_params.append(params)

        # Save output results
        self.all_output_results[output_key] = await func(*all_params)

    def _expand_rely_params(self, params):
        params_list = []
        rely_params = params.get("rely")
        if isinstance(rely_params, list) or isinstance(rely_params, tuple):
            for rely_parm in rely_params:
                expand_params = self._expand_params(rely_parm)
                params_list.extend(expand_params)
        elif isinstance(rely_params, str):
            params_list = self._expand_params(rely_params)
        else:
            raise TypeError("Rely is limited to list, tuple, and string type!")

        return params_list

    def _expand_params(self, rely_params):
        func_output = self.all_output_results.get(rely_params)

        if isinstance(func_output, tuple):
            params = list(func_output)
        elif func_output:
            params = [func_output]
        else:
            params = []

        return params
