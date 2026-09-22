from django.contrib import admin

from .models import Defect, DefectAttachment, DefectComment, DefectTransition


@admin.register(Defect)
class DefectAdmin(admin.ModelAdmin):
    list_display = (
        'code',
        'title',
        'project',
        'version',
        'module',
        'severity',
        'priority',
        'status',
        'assignee',
        'reporter',
        'created_at',
    )
    list_filter = ('status', 'severity', 'priority', 'project', 'version', 'source')
    search_fields = ('code', 'title', 'description', 'module')
    readonly_fields = ('code', 'created_at', 'updated_at', 'resolved_at', 'closed_at')
    date_hierarchy = 'created_at'


@admin.register(DefectTransition)
class DefectTransitionAdmin(admin.ModelAdmin):
    list_display = ('defect', 'from_status', 'to_status', 'operator', 'created_at')
    list_filter = ('from_status', 'to_status')
    search_fields = ('defect__code', 'defect__title', 'comment')
    readonly_fields = ('created_at',)


@admin.register(DefectComment)
class DefectCommentAdmin(admin.ModelAdmin):
    list_display = ('defect', 'author', 'created_at')
    search_fields = ('defect__code', 'defect__title', 'content')
    readonly_fields = ('created_at',)


@admin.register(DefectAttachment)
class DefectAttachmentAdmin(admin.ModelAdmin):
    list_display = ('defect', 'name', 'uploaded_by', 'uploaded_at')
    search_fields = ('defect__code', 'defect__title', 'name')
    readonly_fields = ('uploaded_at',)
