import argparse
import importlib.util
from ..pipeline.pipeline import WorkFlow


def get_argument():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--script_path", default=None,
                        help="The absolute path of the script where the pipeline is located.")
    parser.add_argument("--function_name", default=None,
                        help="Pipeline function name.")
    args = parser.parse_args()
    return args


def load_function_from_file(
    script_path,
    function_name
) -> WorkFlow:
    spec = importlib.util.spec_from_file_location("pipeline", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    function = getattr(module, function_name, None)
    return function


def run_workflow():
    args = get_argument()
    workflow = load_function_from_file(
        args.script_path,
        args.function_name
    )

    workflow.run_in_executor()
