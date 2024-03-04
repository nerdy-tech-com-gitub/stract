import logging
from typing import Optional

from account.models import Domain, Organization
from django.db import IntegrityError

Logger = logging.getLogger(__name__)


class OrganizationService:
    def __init__(self):  # type: ignore
        pass

    @staticmethod
    def get_organization_by_org_id(org_id: str) -> Optional[Organization]:
        try:
            return Organization.objects.get(organization_id=org_id)  # type: ignore
        except Organization.DoesNotExist:
            return None

    @staticmethod
    def create_organization(
        name: str, display_name: str, organization_id: str
    ) -> Organization:
        try:
            organization: Organization = Organization(
                name=name,
                display_name=display_name,
                organization_id=organization_id,
                schema_name=organization_id,
            )
            organization.save()
        except IntegrityError as error:
            Logger.info(f"[Duplicate Id] Failed to create Organization Error: {error}")
            raise error
        # Add one or more domains for the tenant
        domain = Domain()
        domain.domain = organization_id
        domain.tenant = organization
        domain.is_primary = True
        domain.save()
        return organization