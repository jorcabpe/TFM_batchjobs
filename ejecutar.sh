#!/bin/bash

# Cambiar al directorio donde están los archivos .yaml
cd ./Test

# Bucle para aplicar cada archivo .yaml con un delay de 10 segundos
for i in {1..10}; do
  kubectl apply -f batchjob$i.yaml
  echo "Aplicado batchjob$i.yaml"
  sleep 10
done
