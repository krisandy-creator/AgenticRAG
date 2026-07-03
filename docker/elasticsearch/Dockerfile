ARG ELASTIC_VERSION=9.2.0
FROM docker.elastic.co/elasticsearch/elasticsearch:${ELASTIC_VERSION}

ARG ELASTIC_VERSION=9.2.0
RUN elasticsearch-plugin install --batch https://get.infini.cloud/elasticsearch/analysis-ik/${ELASTIC_VERSION}
