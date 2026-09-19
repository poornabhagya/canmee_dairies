from django.conf import settings
from django.db import migrations, models

class Migration(migrations.Migration):
    initial = True
    dependencies = [('masters', '0001_initial'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name='MilkFactor',
            fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),('created_at', models.DateTimeField(auto_now_add=True)),('updated_at', models.DateTimeField(auto_now=True)),('is_deleted', models.BooleanField(default=False)),('date', models.DateField()),('fat', models.DecimalField(decimal_places=2, default=0, max_digits=5)),('snf', models.DecimalField(decimal_places=2, default=0, max_digits=5)),('lr', models.DecimalField(decimal_places=2, default=0, max_digits=6)),('alcohol', models.DecimalField(decimal_places=2, default=0, max_digits=6)),('acidity', models.DecimalField(decimal_places=2, default=0, max_digits=6)),('kq', models.DecimalField(decimal_places=2, default=0, max_digits=6)),('collection_point', models.ForeignKey(on_delete=models.deletion.PROTECT, to='masters.collectionpoint')),('route', models.ForeignKey(on_delete=models.deletion.PROTECT, to='masters.route'))],
            options={'ordering': ['-date', 'route__code']},
        ),
        migrations.CreateModel(
            name='MilkCollection',
            fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),('created_at', models.DateTimeField(auto_now_add=True)),('updated_at', models.DateTimeField(auto_now=True)),('is_deleted', models.BooleanField(default=False)),('date', models.DateField()),('kg', models.DecimalField(decimal_places=2, max_digits=10)),('liters', models.DecimalField(decimal_places=2, editable=False, max_digits=10)),('collection_point', models.ForeignKey(on_delete=models.deletion.PROTECT, to='masters.collectionpoint')),('created_by', models.ForeignKey(on_delete=models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),('route', models.ForeignKey(on_delete=models.deletion.PROTECT, to='masters.route'))],
            options={'ordering': ['-date', 'route__code', 'collection_point__number']},
        ),
        migrations.AddConstraint(model_name='milkfactor', constraint=models.UniqueConstraint(fields=('date', 'route', 'collection_point'), name='uniq_daily_route_point_factor')),
        migrations.AddConstraint(model_name='milkcollection', constraint=models.UniqueConstraint(fields=('date', 'route', 'collection_point'), name='uniq_daily_route_point_collection')),
    ]
