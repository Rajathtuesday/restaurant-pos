from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0045_printjob_tenant_index'),
    ]

    operations = [
        migrations.AddField(
            model_name='printjob',
            name='claimed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]