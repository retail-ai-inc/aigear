from aigear.pipeline.types import RelyParams
import concurrent.futures


def step_executor(step, all_output_results):
    func = step.func
    params = step.params
    output_key = step.output_key

    # If output_key is not set, the output result will be saved using the function name
    if output_key == "":
        output_key = func.__name__

    if params.is_dict:
        # Run based on keyword parameters
        all_params = {}
        for parm in params:
            if isinstance(parm, RelyParams):
                rely_params = _expand_rely_params(parm, all_output_results)
                all_params.update(rely_params)
            else:
                all_params.update(parm)
        # Run and save output results
        results = func(**all_params)
    else:
        # Run based on positional parameters
        all_params = []
        for parm in params:
            if isinstance(parm, RelyParams):
                rely_params = _expand_rely_params(parm, all_output_results)
                all_params.extend(rely_params)
            else:
                all_params.append(parm)
        # Run and save output results
        results = func(*all_params)
    return results


def _expand_rely_params(params, all_output_results):
    rely_params = all_output_results.get(params.rely_output_key)
    # When it is a value, wrap it in a list
    if not isinstance(rely_params, tuple):
        rely_params = [rely_params]

    # By default, all outputs of dependent functions are taken as parameters
    rely_index = params.rely_index
    if not rely_index:
        return rely_params

    if isinstance(rely_index, dict):
        # Creating keyword parameters based on indexes
        rely_params = {key: rely_params[val] for key, val in params.rely_index}
    else:
        # Creating positional parameters based on indexes
        rely_params = [rely_params[idx] for idx in params.rely_index]

    return rely_params


def thread_executor(steps, all_output_results):
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [executor.submit(step_executor, step, all_output_results) for step in steps]
        results_dict = {step.output_key: future.result() for future, step in
                        zip(concurrent.futures.as_completed(futures), steps)}
    return results_dict


def process_executor(steps, all_output_results):
    with concurrent.futures.ProcessPoolExecutor() as executor:
        futures = [executor.submit(step_executor, step, all_output_results) for step in steps]
        results_dict = {step.output_key: future.result() for future, step in
                        zip(concurrent.futures.as_completed(futures), steps)}
    return results_dict
