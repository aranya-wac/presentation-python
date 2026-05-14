from app.models.user import User
from app.models.refresh_token import RefreshToken
from app.models.password_reset import PasswordResetToken
from app.models.brand_kit import BrandKit
from app.models.presentation_version import PresentationVersion
from app.models.theme import Theme
from app.models.template import Template
from app.models.presentation import Presentation
from app.models.generation_job import GenerationJob
from app.models.export_job import ExportJob
from app.models.project import Project, ProjectDocument, ProjectPresentationLink
from app.models.project_activity import ProjectActivity
from app.models.project_member import ProjectMember
from app.models.role_prompt import RolePromptProfile
from app.models.template_slide import TemplateSlide

ALL_MODELS = [
    User,
    RefreshToken,
    PasswordResetToken,
    BrandKit,
    PresentationVersion,
    Theme,
    Template,
    Presentation,
    GenerationJob,
    ExportJob,
    Project,
    ProjectDocument,
    ProjectPresentationLink,
    ProjectActivity,
    ProjectMember,
    RolePromptProfile,
    TemplateSlide,
]
