from django.db import migrations, models

class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name='Route',
            fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),('created_at', models.DateTimeField(auto_now_add=True)),('updated_at', models.DateTimeField(auto_now=True)),('is_deleted', models.BooleanField(default=False)),('code', models.CharField(max_length=20, unique=True)),('name', models.CharField(max_length=120))],
            options={'ordering': ['code']},
        ),
        migrations.CreateModel(
            name='Buyer',
            fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),('created_at', models.DateTimeField(auto_now_add=True)),('updated_at', models.DateTimeField(auto_now=True)),('is_deleted', models.BooleanField(default=False)),('name', models.CharField(max_length=120, unique=True)),('contact_person', models.CharField(blank=True, max_length=120)),('phone', models.CharField(blank=True, max_length=25)),('email', models.EmailField(blank=True, max_length=254)),('address', models.TextField(blank=True))],
        ),
        migrations.CreateModel(
            name='Farmer',
            fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),('created_at', models.DateTimeField(auto_now_add=True)),('updated_at', models.DateTimeField(auto_now=True)),('is_deleted', models.BooleanField(default=False)),('name', models.CharField(max_length=120)),('phone', models.CharField(blank=True, max_length=25)),('address', models.TextField(blank=True))],
        ),
        migrations.CreateModel(
            name='CollectionPoint',
            fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),('created_at', models.DateTimeField(auto_now_add=True)),('updated_at', models.DateTimeField(auto_now=True)),('is_deleted', models.BooleanField(default=False)),('number', models.CharField(max_length=20)),('name', models.CharField(max_length=120)),('location', models.CharField(blank=True, max_length=255)),('route', models.ForeignKey(on_delete=models.deletion.PROTECT, related_name='collection_points', to='masters.route'))],
            options={'ordering': ['route__code', 'number'], 'unique_together': {('number', 'route')}},
        ),
    ]
