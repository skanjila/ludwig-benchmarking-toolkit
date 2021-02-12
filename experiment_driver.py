import argparse
import datetime
import json
import logging
import os
import pickle
import sys
import numpy as np
from copy import deepcopy

import yaml
from ludwig.hyperopt.run import hyperopt

import globals
from build_def_files import *
from database import *
from utils.experiment_utils import *
from utils.metadata_utils import append_experiment_metadata

logging.basicConfig(
    filename='elastic-exp.log', 
    format='%(levelname)s:%(message)s', 
    level=logging.DEBUG
)

def download_data(cache_dir=None):
    data_file_paths = {}
    for dataset in dataset_metadata:
        data_class = dataset_metadata[dataset]['data_class']
        data_path = download_dataset(data_class, cache_dir)
        data_file_paths[dataset] = data_path
    return data_file_paths

def get_gpu_list():
    try:
        return os.environ['CUDA_VISIBLE_DEVICES']
    except KeyError:
        return None

def map_runstats_to_modelpath(hyperopt_training_stats, output_dir, executor='ray'):
    """ 
    maps output of individual hyperopt() run statistics to associated 
    output directories 
    """
    # helper function for finding output folder
    paths = []
    def find_path(d):
        if "model" in os.listdir(d):
            return d
        else:
            for x in os.scandir(d):
                if os.path.isdir(x):
                    path = find_path(x)
                    if path:
                        paths.append(path.path)
            return None

    def compare_dict(d1, d2):
        if type(d1) == list:
            if np.allclose(d1, d2, rtol=1e-04, atol=1e-04): return True
            return False
        else:
            if type(d1) == dict:
                for key, value in d1.items():
                    return compare_dict(value, d2[key])

    def decode_hyperopt_run(run):
        run['training_stats'] = json.loads(run['training_stats'])
        run['parameters'] = json.loads(run['parameters'])
        run['eval_stats'] = json.loads(run['eval_stats'])
        return run
    
    if executor == 'ray': # folder construction is different
        hyperopt_run_metadata = []

        # populate paths
        for x in os.scandir(output_dir):
            if os.path.isdir(x):
                find_path(x)
        
        for hyperopt_run in hyperopt_training_stats:
            hyperopt_run_metadata.append(
                        {
                            'hyperopt_results' : decode_hyperopt_run(hyperopt_run),
                            'model_path' : None
                        }
                    )

        # Populate model_path for respective experiments
        for run_output_dir in paths:
            if os.path.isfile(os.path.join(run_output_dir, \
                    "training_statistics.json")):
                sample_training_stats = json.load(
                    open(
                        os.path.join(run_output_dir, \
                            "training_statistics.json"
                            ), "rb"
                    )
                )
                for i, hyperopt_run in enumerate(hyperopt_run_metadata):
                    try:
                        d_equal = compare_dict(hyperopt_run['hyperopt_results']['training_stats'], \
                                sample_training_stats)
                    except:
                        pass

                    else:
                        if d_equal:
                            hyperopt_run['model_path'] = os.path.join(run_output_dir, \
                                            'model'
                                        )
                            hyperopt_run_metadata[i] = hyperopt_run

    else:
        hyperopt_run_metadata = []
        for run_dir in os.scandir(output_dir):
            if os.path.isdir(run_dir):
                sample_training_stats = json.load(
                    open(
                        os.path.join(run_dir.path, \
                            "training_statistics.json"
                            ), "rb"
                    )
                )
                for hyperopt_run in hyperopt_training_stats:
                    if hyperopt_run['training_stats'] == sample_training_stats:
                        hyperopt_run_metadata.append(
                            {
                                'hyperopt_results' : hyperopt_run,
                                'model_path' : os.path.join(run_dir.path, \
                                        'model'
                                    )
                            }
                        )
    
    return hyperopt_run_metadata

def run_local_experiments(data_file_paths, config_files, es_db=None):
    logging.info("Running hyperopt experiments...")

    # check if overall experiment has already been run
    if os.path.exists(os.path.join(globals.EXPERIMENT_OUTPUT_DIR, \
        '.completed')):
        return 

    for dataset_name, file_path in data_file_paths.items():
        logging.info("Dataset: {}".format(dataset_name))
        for model_config_path in config_files[dataset_name]:
            config_name = model_config_path.split('/')[-1].split('.')[0]
            dataset = config_name.split('_')[1]
            encoder = "_".join(config_name.split('_')[2:])
            experiment_name = dataset + "_" + encoder
            
            
            logging.info("Experiment: {}".format(experiment_name))
            output_dir = os.path.join(globals.EXPERIMENT_OUTPUT_DIR, \
                experiment_name)

            if not os.path.isdir(output_dir):
                os.mkdir(output_dir)
           
            output_dir = os.path.join(globals.EXPERIMENT_OUTPUT_DIR, \
                experiment_name)

            if not os.path.exists(os.path.join(output_dir, '.completed')):
                model_config = load_yaml(model_config_path)
                start = datetime.datetime.now()
                hyperopt_results = hyperopt(
                    model_config,
                    dataset=file_path,
                    model_name=config_name, 
                    #gpus=get_gpu_list(),
                    output_directory=output_dir
                )

                logging.info("time to complete: {}".format(
                    datetime.datetime.now() - start)
                ) 
           
                # Save output locally
                try:
                    pickle.dump(
                        hyperopt_results, 
                        open(os.path.join(
                            output_dir, 
                            f"{dataset}_{encoder}_hyperopt_results.pkl"
                            ),'wb'
                        )
                    )

                    # create .completed file to indicate that experiment
                    # is completed
                    _ = open(os.path.join(output_dir, '.completed'), 'wb')

                except FileNotFoundError:
                    continue

                # save output to db
                if es_db:
                    hyperopt_run_data = map_runstats_to_modelpath(
                        hyperopt_results, output_dir)
                    # ensures that all numerical values are of type float
                    format_fields_float(hyperopt_results)
                    for run in hyperopt_run_data:
                        new_config = substitute_dict_parameters(
                            copy.deepcopy(model_config),
                            parameters=run['hyperopt_results']['parameters']
                        )
                        del new_config['hyperopt']

                        document = {
                            'hyperopt_results': run['hyperopt_results'],
                            'model_path' : run['model_path']
                        }
                        try: 
                            append_experiment_metadata(
                                document, 
                                model_path=run['model_path'], 
                                data_path=file_path
                            )
                        except:
                            pass

                        formatted_document = es_db.format_document(
                            document,
                            encoder=encoder,
                            dataset=dataset,
                            config=model_config
                        )

                        formatted_document['sampled_run_config'] = new_config

                        es_db.upload_document(
                            hash_dict(new_config),
                            formatted_document
                        )

        # create .completed file to indicate that entire hyperopt experiment
        # is completed
        _ = open(os.path.join(globals.EXPERIMENT_OUTPUT_DIR, '.completed'), 'wb')
def main():
    parser = argparse.ArgumentParser(
        description='Ludwig experiments benchmarking driver script',
    )

    parser.add_argument(
        '-hcd',
        '--hyperopt_config_dir',
        help='directory to save all model config',
        type=str,
        default=EXPERIMENT_CONFIGS_DIR
    )
    
    parser.add_argument(
        '-eod',
        '--experiment_output_dir',
        help='directory to save hyperopt runs',
        type=str,
        default=EXPERIMENT_OUTPUT_DIR
    )

    parser.add_argument(
        '-re',
        '--run_environment',
        help='environment in which experiment will be run',
        choices=['local', 'gcp'],
        default='local'
    )
    parser.add_argument(
        '-esc',
        '--elasticsearch_config',
        help='path to elastic db config file',
        type=str,
        default=None
    )
    
    parser.add_argument(
        '-dcd',
        '--dataset_cache_dir',
        help="path to cache downloaded datasets",
        type=str,
        default=None
    )

    # list of encoders to run hyperopt search over : 
    # default is 23 ludwig encoders
    parser.add_argument(
        '-cel',
        '--custom_encoders_list',
        help="list of encoders to run hyperopt experiments on. \
            The default setting is to use all 23 Ludwig encoders",
        nargs='+',
        choices=['all', 'bert', 'rnn'],
        default="all"
    )
    
    args = parser.parse_args()   

    logging.info("Set global variables...")
    set_globals(args) 

    logging.info("GPUs {}".format(os.system('nvidia-smi -L')))

    data_file_paths = download_data(args.dataset_cache_dir)
    logging.info("Building hyperopt config files...")
    config_files = build_config_files()

    es_db = None
    if args.elasticsearch_config is not None:
        logging.info("Set up elastic db...")
        elastic_config = load_yaml(args.elasticsearch_config)
        es_db = Database(
            elastic_config['host'], 
            (elastic_config['username'], elastic_config['password']),
            elastic_config['username'],
            elastic_config['index']
        )
    
    if args.run_environment == 'local' or args.run_environment == 'gcp':
        run_local_experiments(
            data_file_paths, 
            config_files, 
            es_db=es_db
        )

if __name__ == '__main__':
    main()
