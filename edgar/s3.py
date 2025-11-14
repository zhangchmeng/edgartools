import boto3
from botocore.exceptions import ClientError
import logging
import os
from typing import List, Dict, Optional, Union

# Configure logging
logger = logging.getLogger(__name__)


def validate_file_right(content: str|bytes) -> bool:
    """
    Validate if the content is a valid SEC file and not an error page like "Access Denied".
    
    Args:
        content: The content to validate, can be str or bytes.
        
    Returns:
        bool: True if content is valid, False otherwise.
    """
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8")
        except UnicodeDecodeError:
            # Decode failure means invalid content
            return False
    
    # Trim leading/trailing whitespace for easier checks
    content = content.strip()
    
    # Empty content is invalid
    if not content:
        return False
    
    # Check for common Access Denied HTML pages
    if len(content) < 1000:
        if (
            content.startswith("<HTML>") or content.startswith("<!DOCTYPE")
        ) and "Access Denied" in content:
            return False
        
        # Check for typical error keywords
        error_indicators = [
            "Access Denied",
            "You don't have permission to access",
        ]
        for indicator in error_indicators:
            if indicator in content:
                return False
    
    return True

class S3FileHandler:
    """
    S3 file handler for Edgar file caching
    """

    def __init__(
        self,
        bucket_name: Optional[str] = None,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        region_name: str = "us-east-1"
    ):
        """
        Initialize S3 client

        Args:
            bucket_name: S3 bucket name
            aws_access_key_id: AWS access key ID
            aws_secret_access_key: AWS secret access key
            region_name: AWS region name
            
        Raises:
            ValueError: If required S3 configuration is missing
            ClientError: If S3 client initialization fails
        """
        # Use environment variables if not provided
        self.bucket_name = bucket_name or os.environ.get("S3_BUCKET_NAME")
        aws_access_key_id = aws_access_key_id or os.environ.get("S3_AK")
        aws_secret_access_key = aws_secret_access_key or os.environ.get("S3_SK")
        region_name = region_name or os.environ.get("S3_BUCKET_REGION", "us-east-1")
        
        # Validate required configuration
        if not self.bucket_name:
            raise ValueError("S3 bucket name is required. Set S3_BUCKET_NAME environment variable or provide bucket_name parameter.")
        
        if not aws_access_key_id:
            raise ValueError("AWS access key ID is required. Set AWS_ACCESS_KEY_ID environment variable or provide aws_access_key_id parameter.")
            
        if not aws_secret_access_key:
            raise ValueError("AWS secret access key is required. Set AWS_SECRET_ACCESS_KEY environment variable or provide aws_secret_access_key parameter.")
        
        try:
            self.s3_client = boto3.client(
                "s3",
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
                region_name=region_name,
            )
            
            # Test connection by checking if bucket exists
            self._validate_bucket_access()
            
        except Exception as e:
            logger.error(f"Failed to initialize S3 client: {e}")
            raise ClientError(
                error_response={'Error': {'Code': 'S3InitializationError', 'Message': str(e)}},
                operation_name='InitializeS3Client'
            )
    
    def _validate_bucket_access(self):
        """
        Validate that the S3 bucket exists and is accessible.
        
        Raises:
            ClientError: If bucket is not accessible
        """
        try:
            self.s3_client.head_bucket(Bucket=self.bucket_name)
            logger.info(f"Successfully validated access to S3 bucket: {self.bucket_name}")
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == '404':
                logger.error(f"S3 bucket '{self.bucket_name}' does not exist")
                raise ClientError(
                    error_response={'Error': {'Code': 'BucketNotFound', 'Message': f"S3 bucket '{self.bucket_name}' does not exist"}},
                    operation_name='ValidateBucketAccess'
                )
            elif error_code == '403':
                logger.error(f"Access denied to S3 bucket '{self.bucket_name}'")
                raise ClientError(
                    error_response={'Error': {'Code': 'BucketAccessDenied', 'Message': f"Access denied to S3 bucket '{self.bucket_name}'"}},
                    operation_name='ValidateBucketAccess'
                )
            else:
                logger.error(f"Failed to access S3 bucket '{self.bucket_name}': {e}")
                raise

    def upload_file(self, file_path: Optional[str] = None, s3_key: Optional[str] = None, content: Optional[Union[str, bytes]] = None, encoding: str = "utf-8") -> bool:
        """
        Upload file or content to S3

        Args:
            file_path: Local file path (optional if content is provided)
            s3_key: File key name in S3
            content: Content to upload directly (optional if file_path is provided)
            encoding: Encoding format (only used when content is string)

        Returns:
            bool: Whether upload was successful
        """
        if not file_path and content is None:
            logger.error("Either file_path or content must be provided")
            return False
        
        if file_path and not content:
            with open(file_path, 'rb') as read_file:
                content = read_file.read()
            
        if not s3_key:
            logger.error("s3_key must be provided")
            return False

        # If content is provided, use upload_content method
        if content is not None:
            if not validate_file_right(content):
                logger.error("content are 'Access Denied' html")
                return False
            return self.upload_content(content, s3_key, encoding)
        
        # Otherwise, upload from file path
        if file_path and not os.path.exists(file_path):
            logger.error(
                f"Upload failed: Local file {file_path} does not exist"
            )
            return False

        # try:
        #     if file_path:
        #         self.s3_client.upload_file(file_path, self.bucket_name, s3_key)
        #         logger.info(
        #             f"File {file_path} successfully uploaded to {self.bucket_name}/{s3_key}"
        #         )
        #     return True
        # except ClientError as e:
        #     logger.error(f"Failed to upload file: {e}")
        #     return False
        # except Exception as e:
        #     logger.error(f"Unknown error occurred while uploading file: {e}")
        #     return False

    def upload_content(self, content: Union[str, bytes], s3_key: str, encoding: str = "utf-8") -> bool:
        """
        Upload content directly to S3

        Args:
            content: Content to upload, can be string or bytes
            s3_key: File key name in S3
            encoding: Encoding format (only used when content is string)

        Returns:
            bool: Whether upload was successful
        """
        try:
            # Convert string to bytes if necessary
            body = (
                content.encode(encoding)
                if isinstance(content, str)
                else content
            )
            if not validate_file_right(body):
                return False
            
            self.s3_client.put_object(
                Bucket=self.bucket_name, 
                Key=s3_key, 
                Body=body
            )
            logger.info(
                f"Content successfully uploaded to {self.bucket_name}/{s3_key}"
            )
            return True
        except ClientError as e:
            logger.error(f"Failed to upload content: {e}")
            return False
        except Exception as e:
            logger.error(f"Unknown error occurred while uploading content: {e}")
            return False

    def download_file(self, s3_key: str, local_path: str) -> bool:
        """
        Download file from S3

        Args:
            s3_key: File key name in S3
            local_path: Local save path

        Returns:
            bool: Whether download was successful
        """
        if not self.file_exists(s3_key):
            logger.error(f"Download failed: S3 file {s3_key} does not exist")
            return False

        # Ensure target directory exists
        os.makedirs(
            os.path.dirname(os.path.abspath(local_path)), exist_ok=True
        )

        try:
            self.s3_client.download_file(self.bucket_name, s3_key, local_path)
            logger.info(
                f"File {s3_key} successfully downloaded to {local_path}"
            )
            return True
        except ClientError as e:
            logger.error(f"Failed to download file: {e}")
            return False
        except Exception as e:
            logger.error(f"Unknown error occurred while downloading file: {e}")
            return False

    def read_file_content(
        self, s3_key: str, encoding: str = "utf-8"
    ) -> Optional[str]:
        """
        Read S3 file content

        Args:
            s3_key: File key name in S3
            encoding: File encoding format

        Returns:
            Optional[str]: File content, returns None if failed
        """
        if not self.file_exists(s3_key):
            logger.error(f"Read failed: S3 file {s3_key} does not exist")
            return None

        try:
            response = self.s3_client.get_object(
                Bucket=self.bucket_name, Key=s3_key
            )
            content = response["Body"].read().decode(encoding)
            if not validate_file_right(content):
                self.delete_file(s3_key)
                return None
            return content
        except UnicodeDecodeError as e:
            logger.error(
                f"Failed to decode file content, encoding format may not match: {e}"
            )
            return None
        except ClientError as e:
            logger.error(f"Failed to read file content: {e}")
            return None
        except Exception as e:
            logger.error(
                f"Unknown error occurred while reading file content: {e}"
            )
            return None

    def write_file_content(
        self, s3_key: str, content: Union[str, bytes], encoding: str = "utf-8"
    ) -> bool:
        """
        Write content to S3 file

        Args:
            s3_key: File key name in S3
            content: Content to write, can be string or bytes
            encoding: Encoding format (only used when content is string)

        Returns:
            bool: Whether write was successful
        """
        try:
            body = (
                content.encode(encoding)
                if isinstance(content, str)
                else content
            )
            self.s3_client.put_object(
                Bucket=self.bucket_name, Key=s3_key, Body=body
            )
            logger.info(
                f"Content successfully written to {self.bucket_name}/{s3_key}"
            )
            return True
        except ClientError as e:
            logger.error(f"Failed to write file content: {e}")
            return False
        except Exception as e:
            logger.error(
                f"Unknown error occurred while writing file content: {e}"
            )
            return False

    def file_exists(self, s3_key: str) -> bool:
        """
        Check if S3 file exists

        Args:
            s3_key: File key name in S3

        Returns:
            bool: Whether file exists
        """
        try:
            self.s3_client.head_object(Bucket=self.bucket_name, Key=s3_key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            logger.error(f"Error occurred while checking file existence: {e}")
            return False

    def list_files(self, prefix: str = "", delimiter: str = "") -> List[Dict]:
        """
        List files in S3 bucket

        Args:
            prefix: File prefix filter
            delimiter: Delimiter for simulating folder structure

        Returns:
            List[Dict]: List of files, each containing Key, Size, LastModified etc.
        """
        try:
            paginator = self.s3_client.get_paginator("list_objects_v2")
            page_iterator = paginator.paginate(
                Bucket=self.bucket_name, Prefix=prefix, Delimiter=delimiter
            )

            files = []
            for page in page_iterator:
                if "Contents" in page:
                    files.extend(page["Contents"])

            return files
        except ClientError as e:
            logger.error(f"Failed to list files: {e}")
            return []

    def delete_file(self, s3_key: str) -> bool:
        """
        Delete S3 file

        Args:
            s3_key: File key name in S3

        Returns:
            bool: Whether deletion was successful
        """
        if not self.file_exists(s3_key):
            logger.warning(f"Delete failed: S3 file {s3_key} does not exist")
            return False

        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=s3_key)
            logger.info(f"File {s3_key} has been successfully deleted")
            return True
        except ClientError as e:
            logger.error(f"Failed to delete file: {e}")
            return False
        except Exception as e:
            logger.error(f"Unknown error occurred while deleting file: {e}")
            return False
