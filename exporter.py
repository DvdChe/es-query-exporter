#! /usr/bin/env python3

import time
import yaml
import sys
import os
import signal
import setproctitle
import logging
import functools
import operator
import traceback
import re
from pprint import pprint
from logging.handlers import RotatingFileHandler
from prometheus_client import start_http_server, Summary, Gauge
from elasticsearch import Elasticsearch
from elasticsearch import exceptions as es_exceptions
from datetime import datetime


def signal_handler(sig, frame):
    print("You pressed Ctrl+C!")
    shutdown()
    sys.exit(0)


def shutdown():
    # doing something on shutdown
    pass


class es_query_exporter:
    def __init__(self, config: dict):
        """
        self.cfg : dict of global config
        self.gauge_dict : dict containing gauge created
        self.req_dict: dict containing es query responses
        self.logger : object logger from lib
        """
        self.cfg = config
        self.gauge_dict = {}
        self.req_dict = {}
        self.logger = logging.getLogger()

        self.__prepare_logs()

    def __json_extract(self, obj, key):
        """Recursively fetch values from nested JSON."""
        arr = []

        def extract(obj, arr, key):
            """Recursively search for values of key in JSON tree."""
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(v, (dict, list)):
                        extract(v, arr, key)
                    elif k == key:
                        arr.append(v)
            elif isinstance(obj, list):
                for item in obj:
                    extract(item, arr, key)
            return arr

        values = extract(obj, arr, key)
        return values

    def __rgetattr(self, obj, attr, *args):
        """
        Parsing attribute with dot in it
        """

        def get_attr(obj, attr, *args):
            return getattr(obj, attr, *args)

        return functools.reduce(get_attr, [obj] + attr.split("."))

    def __get_export_path(self, export_str: str) -> list:
        """
        convert dict path string doted style into splited list
        convert into int if necessary
        """
        export_path = export_str.split(".")
        for key in range(len(export_path)):
            if re.match("[0-9]", export_path[key]):
                export_path[key] = int(export_path[key])
        return export_path

    def __prepare_logs(self):
        if "loglevel" in self.cfg["exporter"]:
            self.logger.setLevel(
                getattr(logging, self.cfg["exporter"]["loglevel"].upper())
            )
            formatter = logging.Formatter("%(asctime)s :: %(levelname)s :: %(message)s")
            logdir = (
                self.cfg["exporter"]["logdir"]
                if "logdir" in self.cfg["exporter"]
                else os.path.dirname(os.path.realpath(__file__))
            )
            file_handler = RotatingFileHandler(
                logdir + "/es-query-exporter.log", "a", 1000000, 1,
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)

            stream_handler = logging.StreamHandler()
            stream_handler.setLevel(logging.DEBUG)
            self.logger.addHandler(stream_handler)

    def __get_label_names(self, source: dict) -> list:
        """
        parse a source occurence,
        return list of labels name if exists.
        return None if not exist (unlabelled metric)
        """
        labels = []
        for i in source:
            if "labels" in source[i] and labels == []:
                labels = list(source[i]["labels"].keys())
        if labels == []:
            return None

        return list(dict.fromkeys(labels))

    def __parse_source(self, source) -> dict:
        ret = {}
        for source_name in source:
            if "search" in source[source_name].keys():
                try:
                    value = self.__json_extract(
                        self.req_dict[source_name], source[source_name]["search"]
                    )
                    ret["value"] = value[0]
                except Exception as e:
                    self.logger.error(
                        "Error in parsing source ( Search ) %s : %s" % (source_name, e)
                    )
                    self.logger.error("    Parser will set value to -1.")
                    ret["value"] = -1
            elif "export" in source[source_name].keys():
                try:
                    export_path = self.__get_export_path(source[source_name]["export"])
                    ret["value"] = functools.reduce(
                        operator.getitem, export_path, self.req_dict[source_name],
                    )
                except Exception as e:
                    self.logger.error(
                        "Error in parsing source ( Export ) %s : %s" % (source_name, e)
                    )
                    self.logger.error("    Parser will set value to -1.")
                    ret["value"] = -1

            if "labels" in source[source_name]:
                ret["labels"] = source[source_name]["labels"]
        return ret

    def __create_gauge(self):
        """
        Parse metric in self.cfg
        Create gauges object and store it into self.gauge_dict
        """
        for metric in self.cfg["metrics"]:
            for metric_name, metric_param in metric.items():
                # If there is label in metric :
                if len(metric_param["sources"]) > 1:
                    for source in metric_param["sources"]:
                        if metric_name not in self.gauge_dict:
                            labels = self.__get_label_names(source)
                            self.gauge_dict[metric_name] = Gauge(
                                metric_name,
                                metric_param["description"],
                                labelnames=labels,
                            )
                            self.logger.info(
                                "Created labelled metric gauge %s" % (metric_name)
                            )
                elif (
                    len(metric_param["sources"]) == 1
                    and metric_name not in self.gauge_dict
                ):
                    self.gauge_dict[metric_name] = Gauge(
                        metric_name, metric_param["description"]
                    )
                    self.logger.info(
                        "Created unlabelled metric gauge %s" % (metric_name)
                    )

    def __set_labelled_metric(self, metric_name: str, sources: dict):
        """
        Set labelled metrics values by reading es query results
        """
        for source in sources:
            metric_param = self.__parse_source(source)
            try:
                self.gauge_dict[metric_name].labels(**metric_param["labels"]).set(
                    metric_param["value"]
                )
                self.logger.info(
                    "Metric %s ( %s ) updated successfully"
                    % (metric_name, metric_param["labels"])
                )
            except Exception as e:
                self.logger.error(
                    "Unable to export metric %s. metric is set to -1" % (metric_name)
                )
                self.logger.error(e)
                self.gauge_dict[metric_name].labels(**metric_param["labels"]).set(-1)

    def __set_unlabelled_metric(self, metric_name: str, source_dict: dict):
        """
        Set not labelled metrics values by reading es query results
        """
        # get item value from configuration
        metric_param = self.__parse_source(source_dict)
        try:
            self.gauge_dict[metric_name].set(metric_param["value"])
            self.logger.info("Metric %s updated successfully" % (metric_name))
        except Exception as e:
            self.logger.error(
                "Unable to export metric %s. metric is set to -1" % (metric_name)
            )
            self.logger.error(e)
            self.gauge_dict[metric_name].set(-1)

    def __proceed_es_query(self):
        """
        Parsing es requests config to perform requests
        """
        for request in self.cfg["requests"]:
            for req_name, req_param in request.items():
                self.logger.info("Proceeding query %s" % (req_name))
                try:
                    es = Elasticsearch(req_param["server"], retry_on_timeout=False)
                    res = self.__rgetattr(es, req_param["action"])(**req_param["args"])
                except Exception as e:
                    self.logger.error(
                        "Error : Unable to proceed request %s" % (req_name)
                    )
                    self.logger.error(e)
                    res = False
                    pass
                self.req_dict[req_name] = res

    def __export_metric(self):
        """
        Parsing whole metric config to set values from es query results
        """
        for metric in self.cfg["metrics"]:
            for metric_name, metric_param in metric.items():
                if len(metric_param["sources"]) > 1:
                    self.__set_labelled_metric(metric_name, metric_param["sources"])
                elif len(metric_param["sources"]) == 1:
                    self.__set_unlabelled_metric(
                        metric_name, metric_param["sources"][0]
                    )

    def __start_server(self):
        start_http_server(int(self.cfg["exporter"]["port"]))

    def run_exporter(self):
        self.__create_gauge()
        self.__start_server()
        while True:
            self.__proceed_es_query()
            self.__export_metric()
            time.sleep(int(self.cfg["exporter"]["refresh"]))


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    setproctitle.setproctitle("es-query-exporter")
    with open(
        os.path.dirname(os.path.realpath(__file__)) + "/config.yaml", "r"
    ) as yamlconf:
        cfg = yaml.load(yamlconf, Loader=yaml.FullLoader)
    esqe = es_query_exporter(cfg)
    esqe.run_exporter()
