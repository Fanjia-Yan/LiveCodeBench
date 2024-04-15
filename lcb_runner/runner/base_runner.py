import os
import json
from abc import ABC, abstractmethod

from tqdm import tqdm

from lcb_runner.lm_styles import LanguageModel
from lcb_runner.utils.path_utils import get_cache_path
from lcb_runner.utils.multiprocess import run_tasks_in_parallel
from lcb_runner.runner.scenario_router import Scenario


class BaseRunner(ABC):
    def __init__(self, args, model: LanguageModel):
        self.args = args
        self.model = model
        self.client_kwargs: dict[str | str] = {}

        if self.args.use_cache:
            self.cache_path = get_cache_path(model, args)
            if os.path.exists(self.cache_path):
                with open(self.cache_path) as f:
                    self.cache: dict = json.load(f)
            else:
                self.cache = {}
        else:
            self.cache_path = None
            self.cache = None

    def save_cache(self):
        if self.args.use_cache:
            with open(self.cache_path, "w") as f:
                json.dump(self.cache, f, indent=4)

    # @abstractmethod
    def _run_single(self, prompt: str | list[dict[str, str]]) -> list[str]:
        pass

    @staticmethod
    def run_single(combined_args) -> str:
        """
        Run the model for a single prompt and return the output
        Static method to be used in multiprocessing
        Calls the _run_single method with the combined arguments
        """
        prompt: str | list[dict[str, str]]
        cache: dict[str, str]
        call_method: callable
        prompt, cache, args, call_method = combined_args

        if isinstance(prompt, list):
            prompt_cache = json.dumps(prompt)
        if cache is not None and prompt_cache in cache:
            if len(cache[prompt_cache]) == args.n:
                return cache[prompt_cache]

        result = call_method(prompt)
        assert len(result) == args.n

        return result

    def run_batch(self, prompts: list[str | list[dict[str, str]]]) -> list[str]:
        outputs = []
        arguments = [
            (
                prompt,
                self.cache,  ## pass the cache as argument for cache check
                self.args,  ## pass the args as argument for cache check
                self._run_single,  ## pass the _run_single method as argument because of multiprocessing
            )
            for prompt in prompts
        ]
        if self.args.multiprocess > 1:
            parallel_outputs = run_tasks_in_parallel(
                self.run_single,
                arguments,
                self.args.multiprocess,
                use_progress_bar=True,
            )
            for output in parallel_outputs:
                if output.is_success():
                    outputs.append(output.result)
                else:
                    print("Failed to run the model for some prompts")
                    print(output.status)
                    print(output.exception_tb)
                    outputs.extend([""] * self.args.n)
        else:
            outputs = [self.run_single(argument) for argument in tqdm(arguments)]

        if self.args.use_cache:
            for prompt, output in zip(prompts, outputs):
                if isinstance(prompt, list):
                    prompt_cache = json.dumps(prompt)
                self.cache[prompt_cache] = output  ## save the output to cache

        return outputs

    def run_main(self, benchmark: list, format_prompt: callable) -> list:
        if self.args.scenario == Scenario.selfrepair:
            with open(f"output/{self.model.model_repr}/Scenario.codegeneration_10_{self.args.temperature}_eval_all.json") as f:
                check_metadata = json.load(f)
            outputs = []
            for check in tqdm(check_metadata):
                output = []
                checked_base_question_cotent = check["question_content"]
                checked_base_codes = check["code_list"]
                checked_output_codes = check["output_list"]
                checked_base_results = check["graded_list"]
                checked_base_metadata = check["metadata"]
                for i in range(len(checked_base_codes)):
                    prompt=(
                        format_prompt(
                            checked_base_question_cotent,
                            self.model.model_style,
                            checked_base_codes[i],
                            checked_base_results[i],
                            checked_base_metadata[i],
                        )
                    )
                    if prompt == "" or type(prompt) is not list:
                        output.append(checked_output_codes[i])
                    else:
                        output.append(self._run_single(prompt))
                outputs.append(output)
            return outputs
        else:
            prompts = [
                format_prompt(problem, self.model.model_style) for problem in benchmark
            ]
        outputs = self.run_batch(prompts)
        self.save_cache()
        return outputs
