# Elasticsearch prometheus exporter

## Important :

This script is made to export business data stored on an Elastic Search cluster into Prometheus. 
It's made for specific needs. You must only use it only for this purprose. Do not reinvent the wheel, please.   
However, it is pretty simple and you can easily add feature if you need to export other specific business data, so please, feel free to contribute.

![](./img.jpg)

## Configuration :

You must create a config.yaml file in the root folder of the project. 
You can look at `config.example.yaml`.

The `config.yaml` has 3 parts : 

  - `exporter`: This is where you set the basic behaviour of the exporter ( listen port, time refresh, loglevel )
  - `requests`: This is where you configure the Elasticsearch requests you want to collect and export. You specifying here the ES server(s), the index, body of request, and the action to proceed on this request ( \_count, \_search, ... )
  - `metrics`: This is where you have to specify what, and how you want to export the result of a ES requests.


### Exporter :

```yaml
exporter:
  port: 9108
  # Time interval (sec) between actualization of request result
  refresh: 60
  # Possible values are info, warning, debug.
  loglevel: info
```

### Requests :

```yaml
requests:
  # At this level, each occurences below "requests" will instanciate a elasticsearch.Elasticsearch() object  
  # Arbitrary value to identify each instances :
  - my_awesome_request:
      server: "my.es-server.domain.com:9200"
      # The action value will actually call a method provided by the instance.
      # it will call method by this way : Elasticsearch.<action>(**<args>)
      action: count
      args: 
        index: "<logstash-{now/d{yyyy.MM.dd}}>"
        body:
          {
            "query": {
              "bool": {
                "must": [
                    {"match": {"foo": "bar"}}
                  ]
              }
            }
          }

  - my_other_awesome_request:
  	  # If you're working on a cluster you can do this :
      server: 
        - "my.es-server-1.domain.com:9200"
        - "my.es-server-2.domain.com:9200"
        - "my.es-server-3.domain.com:9200"
      action: count
      args:
        index: "<logstash-{now/d{yyyy.MM.dd}}>"
        body:
          {
            "query": {
              "bool": {
                "must": [
                    {"match": {"foo": "bar"}}
                  ]
              }
            }
          }
```

## Metrics:

The exporter will execute the requests consecutively and store each results of each requests into specifics dicts. In this section, you will have to :

- Set a description of the metric, 
- Specify which result you want to use and which element of this result you want to export,
- Set arbitrary labels

```yaml
metrics:
  - my_marvelous_metric:
      # Description of the metric
      description: "awesome requests here"
      # Sources are the request created above
      sources:
      - my_awesome_request:
          # On a _count request, there is a root key in the response containing the result: 
          export: count
          # You can labelize the metric by setting arbitrary key value
          labels:
            spam: egg
            foo: bar
      - my_other_awesome_request:
      	  # You can also access to element to export by specifying path like this
          export: foo.1.bar.element.value
      # !!! IMPORTANT !!! : In the same metric, you have to make sure all label keys have the same name in each time series.
      # Otherwise, the exporter will trig an exception.
          labels:
            spam: hey
            foo: peepoodo
```

