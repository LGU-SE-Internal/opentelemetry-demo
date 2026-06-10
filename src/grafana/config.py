import os

def validate_config():
    tls_enabled = os.getenv("GRAFANA_TLS_ENABLED", "false").lower() == "true"
    mtls_enabled = os.getenv("GRAFANA_MTLS_ENABLED", "false").lower() == "true"
    
    if mtls_enabled and not tls_enabled:
        raise ValueError("mTLS cannot be enabled without TLS being enabled first")
    
    if tls_enabled:
        cert_path = os.getenv("GRAFANA_TLS_CERT_PATH", "")
        key_path = os.getenv("GRAFANA_TLS_KEY_PATH", "")
        if not cert_path.strip():
            raise ValueError("GRAFANA_TLS_CERT_PATH is required when TLS is enabled")
        if not key_path.strip():
            raise ValueError("GRAFANA_TLS_KEY_PATH is required when TLS is enabled")
    
    if mtls_enabled:
        ca_path = os.getenv("GRAFANA_MTLS_CA_CERT_PATH", "")
        if not ca_path.strip():
            raise ValueError("GRAFANA_MTLS_CA_CERT_PATH is required when mTLS is enabled")

def generate_grafana_ini():
    validate_config()
    
    tls_enabled = os.getenv("GRAFANA_TLS_ENABLED", "false").lower() == "true"
    mtls_enabled = os.getenv("GRAFANA_MTLS_ENABLED", "false").lower() == "true"
    
    ini_content = ["[server]"]
    if tls_enabled:
        ini_content.append("protocol = https")
        ini_content.append(f"cert_file = {os.getenv('GRAFANA_TLS_CERT_PATH')}")
        ini_content.append(f"cert_key = {os.getenv('GRAFANA_TLS_KEY_PATH')}")
        if mtls_enabled:
            ini_content.append(f"client_ca_file = {os.getenv('GRAFANA_MTLS_CA_CERT_PATH')}")
            ini_content.append("client_auth_type = RequireAndVerifyClientCert")
    else:
        ini_content.append("protocol = http")
    
    # Read and append rest of the original grafana.ini content, skipping existing [server] section
    with open("/workspace/src/grafana/grafana.ini", "r") as f:
        lines = f.read().splitlines()
        in_server_section = False
        for line in lines:
            if line.startswith("[") and in_server_section:
                in_server_section = False
            if line.strip().startswith("[server]"):
                in_server_section = True
                continue
            if not in_server_section:
                ini_content.append(line)
    
    return "\n".join(ini_content)
