#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Copyright (C) IBM Corporation 2018
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
tester.py:

    - This file sets hosts a function which adds specific arguments a tester will need.
    - Also defines the ``Tester()`` class.


"""
__author__ = "Vincent Marois, Tomasz Kornuta, Younes Bouhadjar"

import os
import torch
from time import sleep
from datetime import datetime

from miprometheus.workers.worker import Worker
from miprometheus.models.model_factory import ModelFactory
from miprometheus.problems.problem_factory import ProblemFactory
from miprometheus.utils.statistics_collector import StatisticsCollector
from miprometheus.utils.statistics_aggregator import StatisticsAggregator


class Tester(Worker):
    """
    Defines the basic ``Tester``.

    If defining another type of tester, it should subclass it.

    """

    def __init__(self, name="Tester"):
        """
        Calls the ``Worker`` constructor, adds some additional params to parser.

       :param name: Name of the worker (DEFAULT: "Tester").
       :type name: str

        """ 
        # Call base constructor to set up app state, registry and add default params.
        super(Tester, self).__init__(name)

        # Add arguments are related to the basic ``Tester``.
        self.parser.add_argument('--visualize',
                                 action='store_true',
                                 dest='visualize',
                                 help='Activate dynamic visualization')

    def setup_global_experiment(self):
        """
        Sets up the global test experiment for the ``Tester``:

            - Checks that the model to use exists on file:

                >>> if not os.path.isfile(flags.model)

            - Checks that the configuration file exists:

                >>> if not os.path.isfile(config_file)

            - Create the configuration:

                >>> self.params.add_config_params_from_yaml(config)

        The rest of the experiment setup is done in :py:func:`setup_individual_experiment()` \
        to allow for multiple tests suppport.

        """
        # Call base method to parse all command line arguments and add default sections.
        super(Tester, self).setup_experiment()

        # Check if model is present.
        if self.flags.model == '':
            print('Please pass path to and name of the file containing model to be loaded as --m parameter')
            exit(-1)

        # Check if file with model exists.
        if not os.path.isfile(self.flags.model):
            print('Model file {} does not exist'.format(self.flags.model))
            exit(-2)

        # Extract path.
        self.abs_path, _ = os.path.split(os.path.dirname(os.path.abspath(self.flags.model)))

        # Check if config file was indicated by the user.
        if self.flags.config != '':
            config_file = self.flags.config
        else:
            # Use the "default one".
            config_file = self.abs_path + '/training_configuration.yaml'

        # Check if configuration file exists.
        if not os.path.isfile(config_file):
            print('Config file {} does not exist'.format(config_file))
            exit(-3)

        # Check the presence of the CUDA-compatible devices.
        if self.flags.use_gpu and (torch.cuda.device_count() == 0):
            self.logger.error("Cannot use GPU as there are no CUDA-compatible devices present in the system!")
            exit(-4)

        # Get the list of configurations which need to be loaded.
        configs_to_load = self.recurrent_config_parse(config_file, [])

        # Read the YAML files one by one - but in reverse order -> overwrite the first indicated config(s)
        self.recurrent_config_load(configs_to_load)

        # -> At this point, the Param Registry contains the configuration loaded (and overwritten) from several files.

    def setup_individual_experiment(self):
        """
        Setup individual test experiment in the case of multiple tests, or the main experiment in the case of \
        one test experiment.


        - Set up the log directory path:

            >>> os.makedirs(self.log_dir, exist_ok=False)

        - Add a FileHandler to the logger (defined in BaseWorker):

            >>>  self.logger.addHandler(fh)

        - Set random seeds:

            >>>  self.set_random_seeds(self.params['testing'], 'testing')

        - Creates problem and model:

            >>> self.problem = ProblemFactory.build_problem(self.params['training']['problem'])
            >>> self.model = ModelFactory.build_model(self.params['model'], self.dataset.default_values)

        - Creates the DataLoader:

            >>> self.dataloader = DataLoader(dataset=self.problem, ...)

        """

        # Get testing problem name.
        try:
            _ = self.params['testing']['problem']['name']
        except KeyError:
            print("Error: Couldn't retrieve the problem name from the 'testing' section in the loaded configuration")
            exit(-5)

        # Get model name.
        try:
            _ = self.params['model']['name']
        except KeyError:
            print("Error: Couldn't retrieve the model name from the loaded configuration")
            exit(-6)
            
        # Prepare output paths for logging
        while True:
            # Dirty fix: if log_dir already exists, wait for 1 second and try again
            try:
                time_str = 'test_{0:%Y%m%d_%H%M%S}'.format(datetime.now())
                if self.flags.savetag != '':
                    time_str = time_str + "_" + self.flags.savetag
                self.log_dir = self.abs_path + '/' + time_str + '/'
                os.makedirs(self.log_dir, exist_ok=False)
            except FileExistsError:
                sleep(1)
            else:
                break

        # Set log dir and add the handler for the logfile to the logger.
        self.log_file = self.log_dir + 'tester.log'
        self.add_file_handler_to_logger(self.log_file)

        # Set random seeds in the testing section.
        self.set_random_seeds(self.params['testing'], 'testing')

        # Check if CUDA is available, if yes turn it on.
        self.check_and_set_cuda(self.flags.use_gpu)

        ################# TESTING PROBLEM ################# 

        # Build test problem and dataloader.
        self.problem, self.sampler, self.dataloader = \
            self.build_problem_sampler_loader(self.params['testing'],'testing') 

        # check if the maximum number of episodes is specified, if not put a
        # default equal to the size of the dataset (divided by the batch size)
        # So that by default, we loop over the test set once.
        max_test_episodes = len(self.dataloader)

        self.params['testing']['problem'].add_default_params({'max_test_episodes': max_test_episodes})
        if self.params["testing"]["problem"]["max_test_episodes"] == -1:
            # Overwrite the config value!
            self.params['testing']['problem'].add_config_params({'max_test_episodes': max_test_episodes})

        # Warn if indicated number of episodes is larger than an epoch size:
        if self.params["testing"]["problem"]["max_test_episodes"] > max_test_episodes:
            self.logger.warning('Indicated maximum number of episodes is larger than one epoch, reducing it.')
            self.params['testing']['problem'].add_config_params({'max_test_episodes': max_test_episodes})

        self.logger.info("Setting the max number of episodes to: {}".format(
            self.params["testing"]["problem"]["max_test_episodes"]))

        ################# MODEL #################

        # Create model object.
        self.model = ModelFactory.build(self.params['model'], self.problem.default_values)

        # Load the pretrained model from checkpoint.
        try: 
            model_name = self.flags.model
            # Load parameters from checkpoint.
            self.model.load(model_name)
        except KeyError:
            self.logger.error("File {} indicated in the command line (--m) seems not to be a valid model checkpoint".format(model_name))
            exit(-5)
        except Exception as e:
            self.logger.error(e)
            # Exit by following the logic: if user wanted to load the model but failed, then continuing the experiment makes no sense.
            exit(-6)

        # Turn on evaluation mode.
        self.model.eval()

        # Move the model to CUDA if applicable.
        if self.app_state.use_CUDA:
            self.model.cuda()

        # Log the model summary.
        self.logger.info(self.model.summarize())

        # Export and log configuration, optionally asking the user for confirmation.
        self.export_experiment_configuration(self.log_dir, "testing_configuration.yaml",self.flags.confirm)

    def initialize_statistics_collection(self):
        """
        Function initializes all statistics collectors and aggregators used by a given worker,
        creates output files etc.
        """
        # Create statistics collector for testing.
        self.testing_stat_col = StatisticsCollector()
        self.add_statistics(self.testing_stat_col)
        self.problem.add_statistics(self.testing_stat_col)
        self.model.add_statistics(self.testing_stat_col)
        # Create the csv file to store the testing statistics.
        self.testing_batch_stats_file = self.testing_stat_col.initialize_csv_file(self.log_dir, 'testing_statistics.csv')

        # Create statistics aggregator for testing.
        self.testing_stat_agg = StatisticsAggregator()
        self.add_aggregators(self.testing_stat_agg)
        self.problem.add_aggregators(self.testing_stat_agg)
        self.model.add_aggregators(self.testing_stat_agg)
        # Create the csv file to store the testing statistic aggregations.
        # Will contain a single row with aggregated statistics.
        self.testing_set_stats_file = self.testing_stat_agg.initialize_csv_file(self.log_dir, 'testing_set_agg_statistics.csv')

    def finalize_statistics_collection(self):
        """
        Finalizes statistics collection, closes all files etc.
        """
        # Close all files.
        self.testing_batch_stats_file.close()
        self.testing_set_stats_file.close()

    def run_experiment(self):
        """
        Main function of the ``Tester``: Test the loaded model over the test set.

        Iterates over the ``DataLoader`` for a maximum number of episodes equal to the test set size.

        The function does the following for each episode:

            - Forwards pass of the model,
            - Logs statistics & accumulates loss,
            - Activate visualization if set.


        """
        # Initialize tensorboard and statistics collection.
        self.initialize_statistics_collection()

        # Set visualization.
        self.app_state.visualize = self.flags.visualize

        # Get number of samples - depending whether using sampler or not.
        if self.params['testing']['dataloader']['drop_last']:
            # if we are supposed to drop the last (incomplete) batch.
            num_samples = len(self.dataloader) * \
                self.params['testing']['problem']['batch_size']
        elif self.sampler is not None:
            num_samples = len(self.sampler)
        else:
            num_samples = len(self.problem)

        self.logger.info('Testing over the entire test set ({} samples in {} episodes)'.format(
            num_samples, len(self.dataloader)))

        try:
            # Run test
            with torch.no_grad():

                episode = 0
                for test_dict in self.dataloader:

                    if episode == self.params["testing"]["problem"]["max_test_episodes"]:
                        break

                    # Evaluate model on a given batch.
                    logits, _ = self.predict_evaluate_collect(self.model, self.problem, 
                                                              test_dict, self.testing_stat_col, episode)

                    # Export to csv - at every step.
                    self.testing_stat_col.export_to_csv()

                    # Log to logger - at logging frequency.
                    if episode % self.flags.logging_interval == 0:
                        self.logger.info(self.testing_stat_col.export_to_string('[Partial Test]'))

                    if self.app_state.visualize:

                        # Allow for preprocessing
                        test_dict, logits = self.problem.plot_preprocessing(test_dict, logits)

                        # Show plot, if user presses Quit - break.
                        self.model.plot(test_dict, logits)

                    # move to next episode.
                    episode += 1

                self.logger.info('\n' + '='*80)
                self.logger.info('Test finished')

                # Export aggregated statistics.
                self.aggregate_and_export_statistics(self.model, self.problem, 
                                                     self.testing_stat_col, self.testing_stat_agg, episode,
                                                     '[Full Test]')

        except SystemExit as e:
            # the training did not end properly
            self.logger.error('Experiment interrupted because {}'.format(e))
        except KeyboardInterrupt:
            # the training did not end properly
            self.logger.error('Experiment interrupted!')
        finally:
            # Finalize statistics collection.
            self.finalize_statistics_collection()

    def check_multi_tests(self):
        """
        Checks if multiple tests are indicated in the testing configuration section.

        .. note::

            If the user would like to run multiple tests, he can use the ``multi_tests`` key in the ``testing`` \
            section to indicate the keys which associated values will be different for each test config.

            E.g.

            >>> # Problem parameters:
            >>> testing:
            >>>     problem:
            >>>         name: SortOfCLEVR
            >>>         batch_size: 64
            >>>         data_folder: '~/data/sort-of-clevr/'
            >>>         dataset_size: 10000
            >>>         split: 'test'
            >>>         img_size: 128
            >>>         regenerate: False
            >>>
            >>>     multi_tests: {batch_size: [64, 128], img_size: [128, 256]}

        .. warning::

            The following constraints apply:

            - Assume that the indicated varying values are **leafs** of the `testing` section
            - The number of indicated varying values per key is the same for all keys
            - The indicated order of the varying values will be respected, i.e. \


             >>>     multi_tests: {batch_size: [64, 128], img_size: [128, 256]}

             and

             >>>     multi_tests: {batch_size: [64, 128], img_size: [256, 128]}

             will lead to different test configs.

            - At least one key has varying values (but this is implicit)


        :return: True if the constraints above are respected, else False

        """
        # check first if the user wants multi-tests
        try:
            _ = self.params['testing']['multi_tests']

            self.logger.info("Checking validity of the indicated values for the multiple tests")
            multi_tests_values = self.params['testing']['multi_tests'].to_dict()

            for key in multi_tests_values:

                # check the key is a leaf of the testing config section
                if not key in list(self.params['testing'].leafs()):
                    self.logger.error("Did not find the indicated key '{}' in the leafs of the 'testing' "
                                      "config section.".format(key))
                    return False

            # check that all indicated list of values have same length
            n_tests = len(next(iter(multi_tests_values.values())))
            if not all(len(x) == n_tests for x in multi_tests_values.values()):
                self.logger.error("Got varying number of elements for the indicated multiple tests values.")

                return False

            # store the number of tests to execute
            self.number_tests = n_tests

            # store the params (and the indicated values) to update
            self.multi_tests_params = multi_tests_values

            # delete them from the param registry
            self.params['testing'].del_config_params(key='multi_tests')

            self.logger.info('Found the following indicated values for multiple tests: {}.'.format(multi_tests_values))
            return True

        except KeyError:
            return False

    def update_config(self, test_index):
        """
        Update ``self.params['testing']`` using the list of values to change for the multiple tests.

        :param test_index: Current test experiment index.
        :type test_index: int

        """
        # If this method is used, then self.number_tests & self.multi_tests_params should be instantiated
        new_params = {k: v[test_index] for k, v in self.multi_tests_params.items()}
        self.logger.warning("Updating the testing config with: {}".format(new_params))

        for leaf_key, new_value in new_params.items():
            self.params['testing'].set_leaf(leaf_key, new_value)

        self.logger.warning("Updated the testing configuration.")
        self.logger.info('\n' + '=' * 80 + '\n')
        return True


def main():
    """
    Entry point function for the ``Tester``.

    """
    tester = Tester()
    # parse args, load configuration and create all required objects.
    tester.setup_global_experiment()

    if tester.check_multi_tests():

        for test_index in range(tester.number_tests):
            tester.logger.info('\n' + '=' * 80 + '\n')
            tester.logger.info("Starting test #{}.".format(test_index+1))
            # update the testing problem config based on the current test index.
            tester.update_config(test_index)

            # finalize the experiment setup
            tester.setup_individual_experiment()

            # run the current experiment
            tester.run_experiment()

            # remove the FileHandler as it will be set again in the next individual test
            tester.logger.removeHandler(tester.logger.handlers[0])

    else:
        # finalize the experiment setup
        tester.setup_individual_experiment()

        # run the experiment
        tester.run_experiment()


if __name__ == '__main__':

    main()
