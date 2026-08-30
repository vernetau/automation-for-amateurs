#!/usr/bin/env bash

# This is the main install script for the Netbox Automation demo
# This script will install Netbox and AWX in a Kubernetes cluster using the official Helm charts.
echo "============== Installing 'Automation-for-Amateurs' =============="

# Check for requried tools
for bin in kubectl helm python3; do
    command -v "$bin" >/dev/null 2>&1 || { echo "Missing required tool: $bin" >&2; exit 1; }
done

# Check if git.env was provided, fail if not
if [ ! -f git.env ]; then
    echo "git.env not found, please create this first before continuing"
    exit 1
fi

# Set namespace, default to "automationForAmateurs"
NAMESPACE="${NAMESPACE:-automation-for-amateurs}"

# Check if secrets.env was provided, generate if not
if [ ! -f secrets.env ]; then
    echo "secrets.env not found, generating..."
    chmod +x generate-secrets.sh
    ./generate-secrets.sh
else
    echo "secrets.env found, using existing..."
fi

# Add env vars
source secrets.env
source versions.env
source git.env

# Check we were provided with the correct git variables, and none are empty
if [ -z "$GIT_USERNAME" ]; then
    echo "Missing required git variable: GIT_USERNAME" >&2
    exit 1
elif  [ -z "$GIT_EMAIL" ]; then
    echo "Missing required git variable: GIT_EMAIL" >&2
    exit 1
elif  [ -z "$GIT_TOKEN" ]; then
    echo "Missing required git variable: GIT_TOKEN" >&2
    exit 1
elif  [ -z "$GIT_ORGANIZATION" ]; then
    echo "Missing required git variable: GIT_ORGANIZATION" >&2
    exit 1
elif  [ -z "$GIT_REPOSITORY" ]; then
    echo "Missing required git variable: GIT_REPOSITORY" >&2
    exit 1
fi

# Assign or create namespace
echo "Using namespace: $NAMESPACE"
kubectl get namespace "$NAMESPACE" >/dev/null 2>&1 || kubectl create namespace "$NAMESPACE"

# Add Helm repos and update
echo "Adding Helm repos..."
helm repo add netbox https://charts.netbox.oss.netboxlabs.com/ >/dev/null
helm repo add awx-operator https://ansible-community.github.io/awx-operator-helm/ >/dev/null
helm repo update >/dev/null

# Install Netbox
echo "Installing NetBox..."
helm upgrade --install netbox netbox/netbox \
    -n "$NAMESPACE" \
    -f netbox-values.yaml \
    --set image.tag="$NETBOX_VERSION" \
    --set superuser.password="${NETBOX_ADMIN_PASSWORD}" \
    --set secretKey="${NETBOX_SECRET_KEY}" \
    --set postgresql.auth.password="${NETBOX_PG_PASSWORD}" \
    --set redis.tasksRedis.password="${NETBOX_REDIS_PASSWORD}" \
    --set redis.cachingRedis.password="${NETBOX_REDIS_PASSWORD}" \

# Create AWX kubernetes secret
echo "Creating AWX admin password..."
kubectl create secret generic awx-admin-password \
    --namespace "$NAMESPACE" \
    --from-literal=password="$AWX_ADMIN_PASSWORD" \
    --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic awx-secret-key \
    --namespace "$NAMESPACE" \
    --from-literal=secret_key="$AWX_SECRET_KEY" \
    --dry-run=client -o yaml | kubectl apply -f -

# Install AWX
echo "Installing AWX controller..."
helm upgrade --install awx-operator awx-operator/awx-operator \
    -n "$NAMESPACE" \

# Install AWX instance
kubectl apply -f awx.yaml

echo "Waiting for pods to come up..."
for i in $(seq 1 90); do
    ready=$(kubectl -n "$NAMESPACE" get pods \
        -o jsonpath='{range .items[?(@.status.phase=="Running")]}{.metadata.name}{"\n"}{end}' \
        2>/dev/null | wc -l || true)
    total=$(kubectl -n "$NAMESPACE" get pods \
        -o jsonpath='{range .items[?(@.status.phase!="Succeeded")]}{.metadata.name}{"\n"}{end}' \
        2>/dev/null | wc -l || true)

    if [ "$total" -gt 0 ] && [ "$ready" -eq "$total" ]; then
        echo "Total pods running: $ready/$total"
        break
    fi

    echo "...still waiting ($ready/$total running), $((i*30))s elapsed"
    sleep 30
done

echo "Netbox and AWX installed."

# Check for existing NetBox API token stored as a secret
EXISTING_TOKEN=$(
    kubectl get secret netbox-api \
        -n "$NAMESPACE" \
        -o jsonpath='{.data.token}' 2>/dev/null || true
)

if [ -n "$EXISTING_TOKEN" ]; then
    NETBOX_API_TOKEN=$(echo "$EXISTING_TOKEN" | base64 -d)
    echo "Using existing NetBox API token."
else
    echo "No existing NetBox API token found."
    echo "Creating NetBox API token..."
    TOKEN_RESPONSE=$(
        kubectl exec -n "$NAMESPACE" deployment/netbox -- \
            curl -s \
            -H "Content-Type: application/json" \
            -X POST \
            "http://netbox/api/users/tokens/provision/" \
            -d '{"version":"2","username":"admin","password":"'$NETBOX_ADMIN_PASSWORD'","description":"Lab automation","enabled":true,"write_enabled":true}'
    )

    FOUND_TOKEN=$(echo "$TOKEN_RESPONSE" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
    FOUND_KEY=$(echo "$TOKEN_RESPONSE" | sed -n 's/.*"key":"\([^"]*\)".*/\1/p')

    if [ -z "$FOUND_TOKEN" ] || [ -z "$FOUND_KEY" ]; then
        echo "ERROR: Failed to create Netbox API token."
        exit 1
    fi

    # Set token for usage
    NETBOX_API_TOKEN="nbt_${FOUND_KEY}.${FOUND_TOKEN}"

    kubectl create secret generic netbox-api \
        --namespace "$NAMESPACE" \
        --from-literal=token="$NETBOX_API_TOKEN" \
        --dry-run=client \
        -o yaml | kubectl apply -f -

    echo "NetBox API token created and saved."
fi

# Create kubernetes secrets
echo "Creating other kubernetes secrets..."
kubectl create secret generic git-credentials \
    --namespace "$NAMESPACE" \
    --from-literal=organization="$GIT_ORGANIZATION" \
    --from-literal=repository="$GIT_REPOSITORY" \
    --from-literal=username="$GIT_USERNAME" \
    --from-literal=email="$GIT_EMAIL" \
    --from-literal=token="$GIT_TOKEN" \
    --dry-run=client \
    -o yaml | kubectl apply -f -

kubectl create secret generic ansible \
    --namespace "$NAMESPACE" \
    --from-literal=repo="$ANSIBLE_CODE_REPO_NAME" \
    --from-literal=username="$ANSIBLE_CODE_REPO_USER" \
    --from-literal=password="$ANSIBLE_CODE_REPO_PASS" \
    --dry-run=client \
    -o yaml | kubectl apply -f -

# Create kubernetes configmap for Netbox seed data
echo "Creating kubernetes configmap for Netbox seed data..."
kubectl create configmap netbox-seed \
    --namespace "$NAMESPACE" \
    --from-file=seed.py=netbox-seed/seed.py \
    --from-file=data.yaml=netbox-seed/data.yaml \
    --dry-run=client \
    -o yaml | kubectl apply -f -

# Start seeding Netbox with data
echo "Starting Netbox seed job..."

# Remove previous job if it exists
kubectl delete job netbox-seed \
    --namespace "$NAMESPACE" \
    --ignore-not-found

# Create seed job
kubectl apply \
    --namespace "$NAMESPACE" \
    -f netbox-seed/job.yaml

# Wait for job
kubectl wait \
    --namespace "$NAMESPACE" \
    --for=condition=complete \
    job/netbox-seed \
    --timeout=300s

# Display output
echo ""
echo "============== NetBox seed output =============="

kubectl logs \
    --namespace "$NAMESPACE" \
    job/netbox-seed

echo ""

if kubectl get secret awx-api -n "$NAMESPACE" >/dev/null 2>&1; then
    echo "AWX API secret already exists."
else
    echo "Creating AWX API token..."
    TOKEN_RESPONSE=$(
        kubectl exec -n "$NAMESPACE" deployment/awx-web -- \
            curl -s \
            -u "admin:$AWX_ADMIN_PASSWORD" \
            -H "Content-Type: application/json" \
            -X POST \
            "http://awx-service/api/v2/users/1/personal_tokens/" \
            -d '{"description":"Lab automation","application":null,"scope":"write"}'
    )

    AWX_TOKEN=$(echo "$TOKEN_RESPONSE" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')

    if [ -z "$AWX_TOKEN" ]; then
        echo "ERROR: Failed to create AWX API token."
        exit 1
    fi

    kubectl create secret generic awx-api \
        --namespace "$NAMESPACE" \
        --from-literal=token="$AWX_TOKEN" \
        --dry-run=client \
        -o yaml | kubectl apply -f -

    echo "AWX API secret created."
fi

if kubectl get secret device-credentials -n "$NAMESPACE" >/dev/null 2>&1; then
    echo "Device credentials already exists."
else
    echo "Creating Device credentials..."
    kubectl create secret generic device-credentials \
        --namespace "$NAMESPACE" \
        --from-literal=username="$DEVICE_USERNANME" \
        --from-literal=password="$DEVICE_PASSWORD" \
        --dry-run=client \
        -o yaml | kubectl apply -f -

    echo "Device credential secret created."
fi

# Create kubernetes configmap for AWX seed data
echo "Creating kubernetes configmap for AWX seed data..."
kubectl create configmap awx-seed \
    --namespace "$NAMESPACE" \
    --from-file=seed.py=awx-seed/seed.py \
    --from-file=data.yaml=awx-seed/data.yaml \
    --dry-run=client \
    -o yaml | kubectl apply -f -

# Start seeding AWX with data
echo "Starting AWX seed job..."
# Remove previous job if it exists
kubectl delete job awx-seed \
    --namespace "$NAMESPACE" \
    --ignore-not-found

# Create seed job
kubectl apply \
    --namespace "$NAMESPACE" \
    -f awx-seed/job.yaml

# Wait for job
kubectl wait \
    --namespace "$NAMESPACE" \
    --for=condition=complete \
    job/awx-seed \
    --timeout=300s

# Display output
echo ""
echo "============== AWX seed output =============="

kubectl logs \
    --namespace "$NAMESPACE" \
    job/awx-seed

echo ""
echo "============== Lab deployment complete =============="