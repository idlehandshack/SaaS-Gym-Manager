# Shop/forms.py

from django import forms
from .models import GlobalProduct, GlobalProductFlavor, GymProduct, GymProductFlavor


class OrderForm(forms.Form):
    quantity = forms.IntegerField(min_value=1)


# ── Gym owner: propose a new product ──────────────────────────────────────

class NewProductForm(forms.Form):
    brand       = forms.CharField(max_length=100, required=False)
    category    = forms.CharField(max_length=100, required=False)
    name        = forms.CharField(max_length=200)
    description = forms.CharField(widget=forms.Textarea, required=False)
    image       = forms.ImageField(required=False)


class FlavorFormSetHelper:
    """
    Parses repeated flavor_name[]/weight[] fields from POST into
    a clean list of dicts. Kept simple (no Django formset) since the
    frontend can just send indexed array fields.
    """
    @staticmethod
    def parse(request) -> list[dict]:
        names   = request.POST.getlist('flavor_name[]')
        weights = request.POST.getlist('weight[]')
        images  = request.FILES.getlist('flavor_image[]')

        flavors = []
        for i, name in enumerate(names):
            name = name.strip()
            if not name:
                continue
            flavors.append({
                'flavor_name': name,
                'weight': weights[i].strip() if i < len(weights) else '',
                'image': images[i] if i < len(images) else None,
            })
        return flavors


# ── Gym owner: edit their own GymProduct / GymProductFlavor ───────────────

class GymProductEditForm(forms.ModelForm):
    class Meta:
        model = GymProduct
        fields = ['custom_description', 'display_order', 'is_visible']


class GymProductFlavorEditForm(forms.ModelForm):
    class Meta:
        model = GymProductFlavor
        fields = [
            'selling_price', 'discount_price', 'cost_price','minimum_stock', 'active',
        ]

    def clean(self):
        cleaned = super().clean()
        selling_price  = cleaned.get('selling_price')
        discount_price = cleaned.get('discount_price')

        for field in ('selling_price', 'discount_price', 'cost_price'):
            value = cleaned.get(field)
            if value is not None and value < 0:
                self.add_error(field, "Cannot be negative.")

        if cleaned.get('stock') is not None and cleaned['stock'] < 0:
            self.add_error('stock', "Cannot be negative.")

        if cleaned.get('minimum_stock') is not None and cleaned['minimum_stock'] < 0:
            self.add_error('minimum_stock', "Cannot be negative.")

        if (
            discount_price is not None
            and selling_price is not None
            and discount_price > selling_price
        ):
            self.add_error('discount_price', "Cannot be greater than selling price.")

        return cleaned


# ── Admin: edit master GlobalProduct data ──────────────────────────────────

class GlobalProductEditForm(forms.ModelForm):
    class Meta:
        model = GlobalProduct
        fields = ['brand', 'category', 'name', 'description', 'image', 'active']


class RejectProductForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea, required=False)


class MergeProductForm(forms.Form):
    winner_id = forms.IntegerField()
    loser_id  = forms.IntegerField()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('winner_id') == cleaned.get('loser_id'):
            raise forms.ValidationError("Winner and loser must be different products.")
        return cleaned
    
class StockAdjustmentForm(forms.Form):
    ADJUSTMENT_TYPES = [
        ('increase', 'Increase Stock (Purchase)'),
        ('decrease', 'Decrease Stock (Adjustment)'),
        ('damage',   'Damage'),
        ('expired',  'Expired'),
        ('returned', 'Returned'),
    ]

    adjustment_type = forms.ChoiceField(choices=ADJUSTMENT_TYPES)
    quantity        = forms.IntegerField(min_value=1)
    reason          = forms.CharField(widget=forms.Textarea, required=False)

    def clean(self):
        cleaned = super().clean()
        adj_type = cleaned.get('adjustment_type')
        reason = cleaned.get('reason', '').strip()
        if adj_type == 'decrease' and not reason:
            self.add_error('reason', "A reason is required for manual stock decreases.")
        return cleaned