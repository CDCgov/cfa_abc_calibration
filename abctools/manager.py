import os
import random

import polars as pl
from gcm_python_wrappers import utils, wrappers

from abctools import abc_methods
from abctools.abc_classes import SimulationBundle


def call_experiment(
    config: str, experiment_mode: str, write=(), **kwargs
) -> SimulationBundle:
    """
    Overall wrapper function to take in pipeline workflow as dictionary and relevant conditions as a config path

    Returns SimulationBundle object
    """

    # Seed for stochastic simulations. Set to None to draw randomly
    if "project_seed" in kwargs:
        if kwargs["project_seed"] is not None:
            seed = kwargs["project_seed"]
            # This should write to config
        else:
            seed = random.randint(0, 2**32 - 1)
    elif experiment_mode == "test":
        # This seed should be preempted with attempt to be read from config
        print("Test mode selected without seed. Defaulting to 0")
        seed = 0
    else:
        raise ValueError("Random seed not specified")

    baseline_params_input = {}

    # Create baseline_params by initializer or loading YAML config into baseline_params
    if "initializer" in kwargs:
        baseline_params = kwargs["initializer"]()
        # Should write a YAML to config
    elif "bundle" in kwargs:
        baseline_params = kwargs["bundle"].baseline_params
        # Should write a YAML to config if not already present
    else:
        baseline_params, summary_string = utils.load_baseline_params(
            config, baseline_params_input
        )

    # Set up Azure client if defined
    if "downloader" in kwargs:
        azure_batch = True
        create_pool = kwargs["create_pool"]

        (
            client,
            blob_container_name,
            job_prefix,
        ) = utils.initialize_azure_client(config, experiment_mode, create_pool)

        if client and blob_container_name and job_prefix:
            print("Azure Client initialized successfully.")
        else:
            print("Failed to initialize Azure Client.")
    else:
        client = None
        blob_container_name = None
        job_prefix = None
        azure_batch = False

    # Create SimulationBundle using inputs - always generated if no bundle passed
    if "bundle" in kwargs:
        # Initialize init_bundle with bundle passed in kwargs
        init_bundle = SimulationBundle(
            inputs=kwargs["bundle"].inputs,
            step_number=kwargs["bundle"].step_number,
            baseline_params=kwargs["bundle"].baseline_params,
            status="duplicated",
        )
    else:
        # If random sampler provided, add inputs generated by sampler function
        if "random_sampler" in kwargs:
            if "sampler_method" in kwargs:
                sampler_method = kwargs["sampler_method"]
            else:
                sampler_method = "sobol"

            # Draw simulation parameters (function call should be user-specified recipe under sampler)
            input_data = abc_methods.draw_simulation_parameters(
                params_inputs=kwargs["random_sampler"],
                n_simulations=kwargs["replicates"],
                method=sampler_method,
                seed=seed,
            )
        else:
            input_data = pl.DataFrame({"simulation": 0, "randomSeed": seed})

        init_bundle = SimulationBundle(
            inputs=input_data,
            step_number=0,
            baseline_params=baseline_params,
        )

    # Directory management with keyed in or default values
    if "wd" in kwargs:
        dir = kwargs["wd"]
    else:
        dir = "."

    # Making specified folders to house simulations if writing
    if len(write) > 0:
        if "preserve" not in kwargs:
            wrappers.delete_experiment_items(dir, experiment_mode, "")
        for sub_dir in write:
            wrappers.gcm_experiments_writer(
                experiments_dir=dir,
                super_experiment_name=experiment_mode,
                sub_experiment_name=sub_dir,
                simulations_dict=init_bundle.writer_input_dict,
                azure_batch=azure_batch,
                azure_client=client,
                blob_container_name=blob_container_name
            )

    # Running simulations if specified
    if "runner" in kwargs:
        if azure_batch:
            raise NotImplementedError("Azure Batch not yet implemented")
        else:
            init_bundle = kwargs["runner"](
                input_bundle=init_bundle
            )
        
            if "summarizer" in kwargs:
                init_bundle.calculate_summary_metrics(
                    summary_function=kwargs["summarizer"]
                )
            
            for sub_dir in write:
                path_name = os.path.join(dir, experiment_mode, sub_dir)
                if sub_dir == "simulations":
                    if init_bundle.results is None:
                        raise ValueError("No simulation results to write")
                    else:
                        for sim_number, sim_data in init_bundle.results.items():
                            file_name = os.path.join(path_name, f"simulation_{sim_number}", "data.csv")
                            sim_data.write_csv(file_name)
                elif sub_dir == "summaries":
                    if init_bundle.summary_metrics is None:
                        raise ValueError("No summary metrics to write")
                    else:
                        for sim_number, sim_data in init_bundle.summary_metrics.items():
                            file_name = os.path.join(path_name, f"simulation_{sim_number}", "report.csv")
                            if isinstance(sim_data, pl.DataFrame):
                                sim_data.write_csv(file_name)
                            else:
                                raise ValueError("Returned summary metrics must be a DataFrame")
                else:
                    raise ValueError("Invalid write option")
                        
                    

    return init_bundle
