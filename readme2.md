# Accessibility LangGraph Scanner - Windows Setup Guide

This guide details how to set up the Accessibility LangGraph Scanner directly from the source code on a Windows laptop. It covers native local execution as well as containerized execution using Docker and Kubernetes (`kubectl`).

---

## Part 1: Running Natively on Windows (from Source Code)

If you meant to run the agent directly on your Windows laptop using the source files in this folder, follow these steps. 

### Prerequisites for Native Setup
1. **Python**: Download and install [Python 3.9+](https://www.python.org/downloads/windows/). Ensure you check the box that says **"Add Python to PATH"** during installation.
2. **Git**: (Optional) Download and install [Git for Windows](https://gitforwindows.org/) to use Git Bash, though Command Prompt or PowerShell works fine.
3. **VS Code**: A good IDE for editing the source code.

### Step-by-Step Installation

1. Open your terminal (PowerShell or Command Prompt) and navigate to the source code directory:
   ```powershell
   cd path\to\Accessibility-agent-web
   ```

2. Create a Python virtual environment to isolate the dependencies:
   ```powershell
   python -m venv .venv
   ```

3. Activate the virtual environment:
   ```powershell
   # In PowerShell:
   .\.venv\Scripts\Activate.ps1
   
   # In Command Prompt:
   .venv\Scripts\activate.bat
   ```

4. Install the application and its dependencies. The `-e` flag installs it in editable mode, which means any changes you make to the source code will take effect immediately.
   ```powershell
   pip install -e .[dev]
   ```

5. Install the required Playwright browsers (like Chromium) used for the scans:
   ```powershell
   playwright install chromium
   ```

6. Add your API Keys and Configuration (for LangGraph / LLM functionality):
   Create a `.env` file in the root folder containing your specific Lite LLM keys:
   ```env
   LLM_GTWY_BASE_URL=http://localhost:4000/v1
   LLM_MODEL=anthropic/claude-sonnet-4-6
   LLM_GTWY_API_KEY=sk-bf-61e6d701-6015-4082-8807-a74b7fa9920d
   ```

### Setting up Lite LLM Port Forwarding via Kubernetes

Since your LLM Gateway is hosted in your AWS EKS cluster, port-forward the service to your local machine so the agent can access it at `localhost:4000`.

Open a new terminal window and run:
```powershell
# Replace 'svc/litellm-service' with the actual name or type of your Lite LLM service/pod in EKS
# You might need to specify the namespace if it's not in the default namespace: -n <namespace>
kubectl port-forward svc/litellm-service 4000:4000
```

*Keep this terminal window open.* This securely routes `localhost:4000` traffic from your Windows laptop directly to the EKS service.

### Running the Scan Natively

Now that setup is complete, you can start running scans from your terminal.

```powershell
# Run the scanner via the CLI:
wcag-scanner --url "https://uat.hdfcsky.com/sky/login" --domain "hdfcsky.com"

# Alternatively, if you want to run the live scan browser visual debugging script:
python live_scan.py
```

The scan results (screenshots, JSON report) will be written to the `artifacts/` folder within the source code directory.

---

## Part 2: Setup kubectl and Run via Docker / Kubernetes

If you want to treat the agent as a containerized job running in a local cluster, follow these steps to set up Docker and `kubectl` on Windows.

### Prerequisites for Kubernetes Setup
1. **Docker Desktop**: Install [Docker Desktop for Windows](https://docs.docker.com/desktop/install/windows-install/). It requires WSL2 (Windows Subsystem for Linux) which Docker will help you install during setup.
2. **Enable Kubernetes in Docker Desktop**:
   - Open the "Docker Desktop" application.
   - Go to **Settings** (gear icon in the top right).
   - Click on the **Kubernetes** tab on the left sidebar.
   - Check **"Enable Kubernetes"** and click **Apply & Restart**. This gives you a local single-node cluster and automatically installs the `kubectl.exe` command-line tool on your Windows PC.

### Step-by-Step Kubernetes Deployment

1. **Build the Docker Image from Source**:
   Open a terminal (PowerShell/CMD) in the `Accessibility-agent-web` directory. Build the container image using the `Dockerfile`:
   ```powershell
   docker build -t accessibility-scanner:latest .
   ```

2. *(Optional)* **Configure Secrets for Kubernetes**:
   If the agent requires an `OPENAI_API_KEY`, create a Kubernetes secret so the cluster can securely pass it to the scanner pod.
   ```powershell
   kubectl create secret generic scanner-secrets --from-literal=OPENAI_API_KEY=sk-yourkeyhere
   ```

3. **Deploy the Scan Job**:
   Use the provided Kubernetes Job manifest to launch the scanner in your local cluster.
   ```powershell
   kubectl apply -f k8s/scan-job.yaml
   ```

4. **Monitor the Scan**:
   Check the status of your Kubernetes job:
   ```powershell
   kubectl get jobs
   kubectl get pods
   ```
   To see the scan executing in real-time, view the logs of the pod created by the job:
   ```powershell
   # Replace the pod name pattern below with the actual pod name output from `kubectl get pods`
   kubectl logs -f job/accessibility-scan-job
   ```

5. **Retrieve Scan Artifacts from the Pod**:
   Because Kubernetes runs the container isolated, the `artifacts/` folder is generated inside the pod. You can copy the artifacts back to your host Windows machine before the pod is deleted:
   ```powershell
   # Assuming your pod name is accessibility-scan-job-xxxxx
   kubectl cp accessibility-scan-job-xxxxx:/app/artifacts ./artifacts_from_k8s
   ```
   *(Note: The `k8s/scan-job.yaml` has `ttlSecondsAfterFinished: 100`, which means the pod logs and artifacts will be automatically deleted 100 seconds after it completes. If you need more time to copy files, remove or increase this setting in `k8s/scan-job.yaml`).*

Happy Scanning!
