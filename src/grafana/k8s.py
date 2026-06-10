import os
from kubernetes.client import V1Service, V1ServiceSpec, V1ServicePort, V1Deployment, V1DeploymentSpec, V1PodTemplateSpec, V1PodSpec, V1Container, V1Volume, V1VolumeMount, V1SecretVolumeSource

def generate_grafana_service():
    tls_enabled = os.getenv("GRAFANA_TLS_ENABLED", "false").lower() == "true"
    ports = []
    if tls_enabled:
        ports.append(V1ServicePort(port=443, target_port=443, name="https"))
    else:
        ports.append(V1ServicePort(port=3000, target_port=3000, name="http"))
    
    return V1Service(
        spec=V1ServiceSpec(
            ports=ports,
            selector={"app": "grafana"}
        )
    )

def generate_grafana_deployment():
    validate_config()
    
    tls_enabled = os.getenv("GRAFANA_TLS_ENABLED", "false").lower() == "true"
    mtls_enabled = os.getenv("GRAFANA_MTLS_ENABLED", "false").lower() == "true"
    
    volumes = []
    volume_mounts = []
    
    if tls_enabled:
        tls_secret_name = os.getenv("GRAFANA_TLS_SECRET_NAME", "grafana-tls")
        cert_path = os.getenv("GRAFANA_TLS_CERT_PATH")
        key_path = os.getenv("GRAFANA_TLS_KEY_PATH")
        
        volumes.append(V1Volume(
            name="grafana-tls-cert",
            secret=V1SecretVolumeSource(
                secret_name=tls_secret_name,
                items=[{"key": "tls.crt", "path": "tls.crt"}]
            )
        ))
        volume_mounts.append(V1VolumeMount(
            name="grafana-tls-cert",
            mount_path=cert_path,
            sub_path="tls.crt",
            read_only=True
        ))
        
        volumes.append(V1Volume(
            name="grafana-tls-key",
            secret=V1SecretVolumeSource(
                secret_name=tls_secret_name,
                items=[{"key": "tls.key", "path": "tls.key"}]
            )
        ))
        volume_mounts.append(V1VolumeMount(
            name="grafana-tls-key",
            mount_path=key_path,
            sub_path="tls.key",
            read_only=True
        ))
    
    if mtls_enabled:
        mtls_ca_secret_name = os.getenv("GRAFANA_MTLS_CA_SECRET_NAME", "grafana-mtls-ca")
        ca_path = os.getenv("GRAFANA_MTLS_CA_CERT_PATH")
        
        volumes.append(V1Volume(
            name="grafana-mtls-ca",
            secret=V1SecretVolumeSource(
                secret_name=mtls_ca_secret_name,
                items=[{"key": "ca.crt", "path": "ca.crt"}]
            )
        ))
        volume_mounts.append(V1VolumeMount(
            name="grafana-mtls-ca",
            mount_path=ca_path,
            sub_path="ca.crt",
            read_only=True
        ))
    
    container = V1Container(
        name="grafana",
        image="grafana/grafana:9.5.0",
        volume_mounts=volume_mounts,
        ports=[{"containerPort": 443 if tls_enabled else 3000}]
    )
    
    return V1Deployment(
        spec=V1DeploymentSpec(
            template=V1PodTemplateSpec(
                spec=V1PodSpec(
                    volumes=volumes,
                    containers=[container]
                )
            )
        )
    )

# Import validate_config from config module
from src.grafana.config import validate_config
