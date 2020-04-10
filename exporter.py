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
from pprint import pprint
from logging.handlers import RotatingFileHandler
from prometheus_client import start_http_server, Summary, Gauge
from elasticsearch import Elasticsearch


def signal_handler(sig, frame):
    print("You pressed Ctrl+C!")
    shutdown()
    sys.exit(0)


def shutdown():
    # doing something on shutdown
    pass


class EsQueryExporter:
    def __init__(self, config):
        self.cfg = config
        self.GaugeDict = {}
        self.ReqDict = {}
        self.logger = logging.getLogger()

        self.prepareLogs()

    def prepareLogs(self):
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

    def getLabelNames(self, source):
        labels = []
        for i in source:
            if "labels" in source[i] and labels == []:
                labels = list(source[i]["labels"].keys())
        if labels == []:
            return None

        return list(dict.fromkeys(labels))

    def createGauge(self):
        for Metric in self.cfg["metrics"]:
            for MetricName, MetricParam in Metric.items():
                # If there is label in metric :
                if len(MetricParam["sources"]) > 1:
                    for source in MetricParam["sources"]:
                        if MetricName not in self.GaugeDict:
                            Labels = self.getLabelNames(source)
                            self.GaugeDict[MetricName] = Gauge(
                                MetricName,
                                MetricParam["description"],
                                labelnames=Labels,
                            )
                            self.logger.info(
                                "Created labelled metric gauge %s" % (MetricName)
                            )
                elif (
                    len(MetricParam["sources"]) == 1
                    and MetricName not in self.GaugeDict
                ):
                    self.GaugeDict[MetricName] = Gauge(
                        MetricName, MetricParam["description"]
                    )
                    self.logger.info(
                        "Created unlabelled metric gauge %s" % (MetricName)
                    )

    def setLabelledMetric(self, MetricName, SourceList):
        for source in SourceList:
            for SourceName, SourceParam in source.items():
                ExportPath = SourceParam["export"].split(".")
                self.GaugeDict[MetricName].labels(**SourceParam["labels"]).set(
                    functools.reduce(
                        operator.getitem, ExportPath, self.ReqDict[SourceName]
                    )
                )

    def setUnlabelledMetric(self, MetricName, SourceDict):
        for SourceName, SourceParam in SourceDict.items():
            ExportPath = SourceParam["export"].split(".")
            self.GaugeDict[MetricName].set(
                functools.reduce(operator.getitem, ExportPath, self.ReqDict[SourceName])
            )

    def exportMetrics(self):
        for Metric in self.cfg["metrics"]:
            for MetricName, MetricParam in Metric.items():
                if len(MetricParam["sources"]) > 1:
                    self.setLabelledMetric(MetricName, MetricParam["sources"])
                elif len(MetricParam["sources"]) == 1:
                    self.setUnlabelledMetric(MetricName, MetricParam["sources"][0])

    def proceedEsQuery(self):
        for Request in self.cfg["requests"]:
            for ReqName, ReqParam in Request.items():
                self.logger.info("Proceeding query %s" % (ReqName))
                try:
                    ReqBody = ReqParam["body"] if "body" in ReqParam else None
                    es = Elasticsearch(ReqParam["server"], retry_on_timeout=False)
                    res = getattr(es, ReqParam["action"])(
                        index=ReqParam["index"], body=ReqBody
                    )
                except:
                    self.logger.error("Error : Unable to proceed request")
                    res = False
                    pass

                self.ReqDict[ReqName] = res

    def startHTTPServer(self):
        start_http_server(int(self.cfg["exporter"]["port"]))

    def runExporter(self):
        self.createGauge()
        self.startHTTPServer()
        while True:
            self.proceedEsQuery()
            self.exportMetrics()
            time.sleep(int(self.cfg["exporter"]["refresh"]))


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    setproctitle.setproctitle("es-query-exporter")
    with open(
        os.path.dirname(os.path.realpath(__file__)) + "/config.yaml", "r"
    ) as yamlconf:
        cfg = yaml.load(yamlconf, Loader=yaml.FullLoader)
    esqe = EsQueryExporter(cfg)
    esqe.runExporter()
