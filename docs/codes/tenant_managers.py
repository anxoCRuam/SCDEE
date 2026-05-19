class TenantManager(models.Manager):
    def get_queryset(self) -> models.QuerySet:
        qs = super().get_queryset()
        org_id = get_current_organization_id()
        if org_id is not None:
            return qs.filter(organization_id=org_id)
        return qs
