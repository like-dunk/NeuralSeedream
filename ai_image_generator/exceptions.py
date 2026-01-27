"""
自定义异常类
"""


class GeneratorError(Exception):
    """生成器基础异常"""
    pass


class ConfigurationError(GeneratorError):
    """配置错误"""
    
    def __init__(self, message: str, field: str = None):
        self.field = field
        super().__init__(message)


class TemplateRenderError(GeneratorError):
    """模板渲染错误"""
    
    def __init__(self, message: str, template: str = None):
        self.template = template
        super().__init__(message)


class PathNotFoundError(GeneratorError):
    """路径不存在错误"""
    
    def __init__(self, path: str, message: str = None):
        self.path = path
        msg = message or f"路径不存在: {path}"
        super().__init__(msg)


class APIError(GeneratorError):
    """API调用错误"""
    
    def __init__(self, message: str, task_id: str = None, status_code: int = None):
        self.task_id = task_id
        self.status_code = status_code
        super().__init__(message)


class MOSSError(GeneratorError):
    """MOSS存储错误"""
    
    def __init__(self, message: str, moss_id: str = None):
        self.moss_id = moss_id
        super().__init__(message)


class SelectionError(GeneratorError):
    """选择错误"""
    
    def __init__(self, message: str, available: int = None, requested: int = None):
        self.available = available
        self.requested = requested
        super().__init__(message)
