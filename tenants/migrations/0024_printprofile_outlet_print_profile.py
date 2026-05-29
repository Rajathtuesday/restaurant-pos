from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0023_add_printjob'),
    ]

    operations = [
        migrations.CreateModel(
            name='PrintProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('kot_large_font', models.BooleanField(default=True, help_text='Items print double-height — easier for chefs to read at a glance.')),
                ('kot_show_total', models.BooleanField(default=True, help_text='Print the sum of KOT item prices at the bottom of the KOT.')),
                ('bill_inner_margin', models.PositiveSmallIntegerField(default=4, help_text='Characters removed from paper width to create side margins on the bill. 4 = ~2-char margin each side on 80mm paper. 0 = full width.')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='print_profiles', to='tenants.tenant')),
            ],
            options={
                'ordering': ['tenant', 'name'],
            },
        ),
        migrations.AddConstraint(
            model_name='printprofile',
            constraint=models.UniqueConstraint(fields=['tenant', 'name'], name='unique_print_profile_per_tenant'),
        ),
        migrations.AddField(
            model_name='outlet',
            name='print_profile',
            field=models.ForeignKey(blank=True, help_text='Receipt and KOT print format for this outlet. Leave blank to use defaults.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='outlets', to='tenants.printprofile'),
        ),
    ]
