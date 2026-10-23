class EngineeringMemoryError(Exception):
    """Base exception for Engineering Memory platform."""
    pass


class ConfigurationError(EngineeringMemoryError):
    """Raised when required environment configurations or credentials are missing."""
    pass


class HindsightMemoryError(EngineeringMemoryError):
    """Raised when an operation with Hindsight memory service fails."""
    pass


class SupabaseStateError(EngineeringMemoryError):
    """Raised when an operation with Supabase state store fails."""
    pass


class GitHubError(EngineeringMemoryError):
    """Base exception for GitHub API errors."""
    pass


class GitHubAuthError(GitHubError):
    """Raised when GitHub token is missing, invalid, or expired."""
    pass


class GitHubNotFoundError(GitHubError):
    """Raised when GitHub repository or PR is not found."""
    pass


class GitHubRateLimitError(GitHubError):
    """Raised when GitHub API rate limit is exceeded."""
    pass


class GroqServiceError(EngineeringMemoryError):
    """Base exception for Groq LLM service errors."""
    pass


class GroqAuthError(GroqServiceError):
    """Raised when Groq API key is missing, invalid, or unauthorized."""
    pass


class GroqRateLimitError(GroqServiceError):
    """Raised when Groq API rate limit or TPM/RPM is exceeded."""
    pass


class GroqResponseError(GroqServiceError):
    """Raised when Groq returns an empty or unparseable response."""
    pass


