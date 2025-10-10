#!/bin/bash

# Color codes for output
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

# Print script usage
print_usage() {
    echo "Usage: ./dev.sh -c container_tool [-up|-down]"
    echo ""
    echo "Required Options:"
    echo "  -c     Container tool (finch or docker)"
    echo ""
    echo "Action Options (one required):"
    echo "  -up    Start development environment"
    echo "  -down  Stop development environment"
    echo ""
    echo "Additional Options:"
    echo "  -h     Show this help message"
    echo ""
    echo "Examples:"
    echo "  ./dev.sh -c docker -up    # Start development environment"
    echo "  ./dev.sh -c docker -down  # Stop development environment"
}

# Initialize variables
CONTAINER_TOOL=""
ACTION=""

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -c)
            CONTAINER_TOOL="$2"
            shift 2
            ;;
        -up)
            ACTION="up"
            shift
            ;;
        -down)
            ACTION="down"
            shift
            ;;
        -h)
            print_usage
            exit 0
            ;;
        *)
            print_usage
            exit 1
            ;;
    esac
done

# Validate container tool is provided
if [ -z "$CONTAINER_TOOL" ]; then
    echo "❌ Error: Container tool (-c) is required. Must be either 'finch' or 'docker'"
    print_usage
    exit 1
fi

# Validate container tool value
if [[ "$CONTAINER_TOOL" != "finch" && "$CONTAINER_TOOL" != "docker" ]]; then
    echo "❌ Invalid container tool. Must be either 'finch' or 'docker'"
    print_usage
    exit 1
fi

# Validate action is provided
if [ -z "$ACTION" ]; then
    echo "❌ Error: Action (-up or -down) is required"
    print_usage
    exit 1
fi

# Function to check if a command exists
check_command() {
    if ! command -v "$1" &> /dev/null; then
        echo -e "❌ Error: $1 is not installed"
        return 1
    else
        echo -e "✅ $1 is installed"
        return 0
    fi
}

# Function to check Docker daemon
check_docker_daemon() {
    echo "Checking Docker setup..."
    
    # First check if Docker client exists
    if ! command -v docker &> /dev/null; then
        echo "❌ Docker is not installed"
        if [[ "$OSTYPE" == "darwin"* ]]; then
            echo "Please install Docker Desktop for Mac from https://www.docker.com/products/docker-desktop"
        elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
            echo "Please install Docker using your distribution's package manager"
            echo "For Ubuntu/Debian: sudo apt-get install docker.io"
            echo "For RHEL/CentOS: sudo yum install docker"
        else
            echo "Please install Docker and try again"
        fi
        return 1
    fi

    # Try to get Docker server version and capture the output and exit status
    local server_version
    local exit_status
    
    server_version=$(docker info --format '{{.ServerVersion}}' 2>&1)
    exit_status=$?

    # Check if the command was successful and if the output doesn't contain error messages
    if [ $exit_status -ne 0 ] || [[ $server_version == *"Cannot connect"* ]] || [[ $server_version == *"error"* ]] || [[ $server_version == *"command not found"* ]]; then
        echo "❌ Docker daemon is not running"
        if [[ "$OSTYPE" == "darwin"* ]]; then
            echo "Please start Docker Desktop for Mac and try again"
            echo "You can start it from the Applications folder or menu bar icon"
        elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
            echo "Please start Docker daemon (dockerd) and try again"
            echo "You can start it with: sudo systemctl start docker"
        else
            echo "Please start Docker daemon and try again"
        fi
        echo "Error details: $server_version"
        return 1
    fi
    
    echo "✅ Docker daemon is running (Server Version: $server_version)"
    return 0
}

# Function to check and start Finch VM if needed
check_and_start_finch_vm() {
    echo "Checking Finch VM status..."
    if ! finch vm status | grep -q "Running"; then
        echo -e "${YELLOW}Finch VM is not running. Starting it now...${NC}"
        if ! finch vm start; then
            echo -e "❌ Error: Failed to start Finch VM"
            return 1
        fi
        
        # Wait for VM to be fully ready
        echo "Waiting for Finch VM to be ready..."
        sleep 5
        
        if ! finch vm status | grep -q "Running"; then
            echo -e "❌ Error: Finch VM failed to start properly"
            return 1
        fi
    fi
    echo -e "✅ Finch VM is running"
    return 0
}

# Function to check Node.js version
check_node_version() {
    local required_version="18"
    local current_version=$(node -v | cut -d'v' -f2 | cut -d'.' -f1)
    
    if [ "$current_version" -lt "$required_version" ]; then
        echo -e "❌ Error: Node.js version must be v${required_version} or higher"
        echo -e "${YELLOW}Current version: $(node -v)${NC}"
        return 1
    else
        echo -e "✅ Node.js version $(node -v) is compatible"
        return 0
    fi
}

# Function to check npm installation
check_npm() {
    if ! npm -v &> /dev/null; then
        echo -e "❌ Error: npm is not installed"
        return 1
    else
        echo -e "✅ npm is installed ($(npm -v))"
        return 0
    fi
}

# Function to load environment variables
load_env_file() {
    if [ -f .env ]; then
        echo "Loading environment variables from .env file..."
        
        # Read each line from .env file
        while IFS= read -r line || [ -n "$line" ]; do
            # Skip comments and empty lines
            if [[ $line =~ ^[^#].+=.+ ]]; then
                # Trim trailing whitespace and carriage returns
                line="${line%"${line##*[![:space:]]}"}"  # Remove trailing whitespace
                line="${line%$'\r'}"  # Remove trailing carriage return if present
                
                # Export the variable
                export "${line?}"
            fi
        done < .env
    else
        echo "❌ Error: .env file not found"
        echo "Please create a .env file with required AWS credentials"
        return 1
    fi
}

# Function to check AWS credentials
check_aws_credentials() {
    # First, load the environment variables
    load_env_file || return 1

    # Check if required variables are set
    if [ -z "$AWS_ACCESS_KEY_ID" ] || [ -z "$AWS_SECRET_ACCESS_KEY" ]; then
        echo -e "❌ Error: AWS credentials not found in .env file"
        echo -e "${YELLOW}Please ensure AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are set in your .env file${NC}"
        return 1
    fi

    # Check if session token is provided when using access keys
    if [ -z "$AWS_SESSION_TOKEN" ]; then
        echo -e "⚠️ Warning: AWS_SESSION_TOKEN not defined in .env file"
        echo -e "${YELLOW}Is recommended to use temporary credentials with a session token for enhanced security${NC}"
        echo -e "${YELLOW}You can obtain these through AWS SSO, STS, or role assumption${NC}"
    fi

    # Verify credentials work and are temporary
    echo "Verifying AWS credentials..."
    if ! aws sts get-caller-identity &> /dev/null; then
        echo -e "❌ Error: AWS credentials are invalid or expired"
        echo -e "${YELLOW}Please ensure your credentials are valid and not expired${NC}"
        return 1
    fi

    # Check if using temporary credentials by looking at the caller identity
    local caller_identity
    caller_identity=$(aws sts get-caller-identity --output json)
    local arn
    arn=$(echo "$caller_identity" | grep -o '"Arn": "[^"]*' | cut -d'"' -f4)
    
    if echo "$arn" | grep -q "assumed-role\|federated-user"; then
        echo -e "✅ Using valid temporary AWS credentials"
    else
        echo -e "⚠️  Warning: It appears you're not using temporary credentials"
        echo -e "${YELLOW}For security reasons, please use temporary credentials with a session token${NC}"
    fi

    return 0
}

# Function to setup dependencies
setup_dependencies() {
    echo "📦 Setting up dependencies..."
    
    # Create and activate Python virtual environment
    echo "Creating Python virtual environment..."
    python3 -m venv .venv
    
    # Activate virtual environment
    echo "Activating virtual environment..."
    source .venv/bin/activate
    
    # Install Python dependencies
    echo "Installing Python dependencies..."
    pip3 install --upgrade pip
    pip3 install -r requirements.txt
    
    # Install CDK dependencies
    echo "Installing CDK dependencies..."
    npm install
    
    # Install frontend dependencies
    echo "Installing frontend dependencies..."
    cd ecs_fargate_app/frontend
    npm install
    cd ../..
    
    # Install backend dependencies
    echo "Installing backend dependencies..."
    cd ecs_fargate_app/backend
    npm install
    cd ../..
    
    echo "✅ Dependencies setup completed"
}

# Function to start development environment
start_dev_environment() {
    echo "Starting development environment..."
    
    # Initialize error flag
    has_error=0

    # Perform all checks
    check_command "node" || has_error=1
    check_command "npm" || has_error=1
    check_command "aws" || has_error=1
    check_command "$CONTAINER_TOOL" || has_error=1

    if [ "$CONTAINER_TOOL" = "docker" ]; then
        check_docker_daemon || has_error=1
    else
        check_and_start_finch_vm || has_error=1
    fi

    check_node_version || has_error=1
    check_npm || has_error=1
    check_aws_credentials || has_error=1
    
    # Exit if any checks failed
    if [ $has_error -eq 1 ]; then
        echo -e "\n❌ Environment verification failed. Please fix the above issues and try again."
        exit 1
    fi

    setup_dependencies

    echo -e "\n✅ Environment verification completed successfully!"

    # Start the containers
    if [ "$CONTAINER_TOOL" = "docker" ]; then
        docker compose -f finch-compose.dev.yaml up --build
        trap 'docker compose -f finch-compose.dev.yaml down' EXIT
    else
        finch compose -f finch-compose.dev.yaml up --build
        trap 'finch compose -f finch-compose.dev.yaml down' EXIT
    fi
}

# Function to stop development environment
stop_dev_environment() {
    echo "Stopping development environment..."
    
    if [ "$CONTAINER_TOOL" = "docker" ]; then
        docker compose -f finch-compose.dev.yaml down
    else
        finch compose -f finch-compose.dev.yaml down
    fi
    
    echo "✅ Development environment stopped successfully"
}

# Main execution
echo "Development environment management"
echo "Using container tool: $CONTAINER_TOOL"
echo "Action: $ACTION"

case $ACTION in
    "up")
        start_dev_environment
        ;;
    "down")
        stop_dev_environment
        ;;
    *)
        echo "❌ Invalid action specified"
        print_usage
        exit 1
        ;;
esac