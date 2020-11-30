ENCODER_CONFIG_DIR = './encoder-configs'
EXPERIMENT_CONFIGS_DIR = './hyperopt-experiment-configs'

ENCODER_HYPEROPT_FILENAMES = {
    'bert': 'bert_hyperopt.yaml',
    'rnn' : 'rnn_hyperopt.yaml'
}

ENCODER_FILE_LIST = ENCODER_HYPEROPT_FILENAMES.values()

CONFIG_TEMPLATE_FILE = './experiment-templates/config_template.yaml'
DATASET_METADATA_FILE = './experiment-templates/dataset_metadata.yaml'
HYPEROPT_CONFIG_FILE = './experiment-templates/hyperopt_config.yaml'
EXPERIMENT_OUTPUT_DIR = './experiment-outputs'
