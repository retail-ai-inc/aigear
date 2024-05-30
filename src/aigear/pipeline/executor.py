from aigear.pipeline.types import RelyParams
import concurrent.futures


def step_executor(step, all_output_results):
    func = step.func
    params = step.params
    output = step.output_key

    all_params = []
    for parm in params:
        if isinstance(parm, RelyParams):
            rely_params = _expand_rely_params(parm, all_output_results)
            all_params.extend(rely_params)
        else:
            all_params.append(parm)
    # Run and save output results
    outputs = func(*all_params)

    if output.keywords:
        # Store parameters according to keywords
        results = {key: output for key, output in zip(output.keywords, outputs)}
    else:
        # When the output is a value, wrap it into a tuple
        results = outputs
    return results


def _expand_rely_params(params, all_output_results):
    rely_params = all_output_results.get(params.rely_output_key)
    if params.keywords:
        expanded_params = [rely_params[key] for key in params.keywords]
    elif isinstance(rely_params, tuple):
        expanded_params = rely_params
    else:
        expanded_params = [rely_params]
    return expanded_params


def thread_executor(steps, all_output_results):
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [executor.submit(step_executor, step, all_output_results) for step in steps]
        results_dict = {step.output_key.key: future.result() for future, step in
                        zip(concurrent.futures.as_completed(futures), steps)}
    return results_dict


def process_executor(steps, all_output_results):
    with concurrent.futures.ProcessPoolExecutor() as executor:
        futures = [executor.submit(step_executor, step, all_output_results) for step in steps]
        results_dict = {step.output_key.key: future.result() for future, step in
                        zip(concurrent.futures.as_completed(futures), steps)}
    return results_dict
