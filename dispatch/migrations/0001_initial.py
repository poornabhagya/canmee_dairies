from django.db import migrations, models

class Migration(migrations.Migration):
    initial = True
    dependencies = [('masters', '0001_initial')]
    operations = [
        migrations.CreateModel(
            name='MilkDistribution',
            fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),('created_at', models.DateTimeField(auto_now_add=True)),('updated_at', models.DateTimeField(auto_now=True)),('is_deleted', models.BooleanField(default=False)),('date', models.DateField()),('dispatch_no', models.CharField(max_length=40)),('lorry_no', models.CharField(blank=True, max_length=40)),('driver', models.CharField(blank=True, max_length=120)),('kg', models.DecimalField(decimal_places=2, max_digits=10)),('fat', models.DecimalField(decimal_places=2, default=0, max_digits=5)),('snf', models.DecimalField(decimal_places=2, default=0, max_digits=5)),('lr', models.DecimalField(decimal_places=2, default=0, max_digits=6)),('alcohol', models.DecimalField(decimal_places=2, default=0, max_digits=6)),('acidity', models.DecimalField(decimal_places=2, default=0, max_digits=6)),('buyer', models.ForeignKey(on_delete=models.deletion.PROTECT, to='masters.buyer')),('collection_point', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.PROTECT, to='masters.collectionpoint')),('route', models.ForeignKey(on_delete=models.deletion.PROTECT, to='masters.route'))],
            options={'ordering': ['-date', 'buyer__name']},
        ),
    ]
