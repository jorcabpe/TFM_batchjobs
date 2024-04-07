import kopf
import kubernetes.client
from kubernetes.client.rest import ApiException
import yaml

@kopf.on.create('batch.upv.es', 'v1', 'batchjobs')
def create_fn(spec, **kwargs):
    try:
        
        # Después de crear el recurso personalizado, puedes asignar la fase y el mensaje
        # a través de la API de Kubernetes
        api_instance = kubernetes.client.CustomObjectsApi()
        
        # Define el nombre del recurso personalizado y el espacio de nombres si es aplicable
        namespace = "default"  # Cambia esto según tu caso
        plural = "batchjobs"
        name = kwargs['body']['metadata']['name']

        # Define el cuerpo del recurso personalizado con la fase y el mensaje
        body = {
            "apiVersion": "batch.upv.es/v1",
            "kind": "BatchJob",
            "metadata": {
                "name": name,
                "namespace": namespace  # Cambia esto según tu caso
            },
            "status": {
                "phase": "Pending",  # Cambia esto según la fase que desees asignar
                "message": "El recurso personalizado se ha creado exitosamente."  # Cambia esto según el mensaje que desees asignar
            }
        }

        # Actualiza el estado del recurso personalizado
        api_instance.patch_namespaced_custom_object(
            group="batch.upv.es",
            version="v1",
            namespace=namespace,  # Cambia esto según tu caso
            plural=plural,
            name=name,
            body=body

    except ApiException as e:
        print("Exception when calling CustomObjectsApi->patch_namespaced_custom_object: %s\n" % e)
      