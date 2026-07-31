from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Expense(models.Model):
    """
    A single business expense entry for a gym.
    Lives entirely in its own app — does NOT touch billing/Payment/Invoice/Revenue.
    """

    class Category(models.TextChoices):
        RENT          = 'rent',          '🏠 Rent'
        SALARY        = 'salary',        '👨 Staff Salary'
        ELECTRICITY   = 'electricity',   '⚡ Electricity'
        WATER         = 'water',         '💧 Water'
        INTERNET      = 'internet',      '🌐 Internet'
        CLEANING      = 'cleaning',      '🧹 Cleaning'
        MAINTENANCE   = 'maintenance',   '🛠 Maintenance'
        EQUIPMENT     = 'equipment',     '🏋 Equipment'
        MARKETING     = 'marketing',     '📢 Marketing'
        SOFTWARE      = 'software',      '💻 Software Subscription'
        TAX           = 'tax',           '🧾 Tax'
        REFRESHMENTS  = 'refreshments',  '🥤 Refreshments'
        OFFICE        = 'office',        '📦 Office Supplies'
        TRANSPORT     = 'transport',     '🚗 Transport'
        REPAIR        = 'repair',        '🔧 Repair'
        LAUNDRY       = 'laundry',       '🧺 Laundry'
        SECURITY      = 'security',      '🛡 Security'
        MISCELLANEOUS = 'miscellaneous', '➕ Other'

    # Default title shown in the quick-entry chip UI when a category is picked.
    CATEGORY_DEFAULT_TITLES = {
        Category.RENT:          'Monthly Gym Rent',
        Category.SALARY:        'Staff Salary',
        Category.ELECTRICITY:   'Electricity Bill',
        Category.WATER:         'Water Bill',
        Category.INTERNET:      'Internet Bill',
        Category.CLEANING:      'Cleaning Expense',
        Category.MAINTENANCE:   'Maintenance Expense',
        Category.EQUIPMENT:     'Gym Equipment Purchase',
        Category.MARKETING:     'Marketing Expense',
        Category.SOFTWARE:      'Software Subscription',
        Category.TAX:           'Tax Payment',
        Category.REFRESHMENTS:  'Refreshments',
        Category.OFFICE:        'Office Supplies',
        Category.TRANSPORT:     'Transport Expense',
        Category.REPAIR:        'Equipment Repair',
        Category.LAUNDRY:       'Laundry Expense',
        Category.SECURITY:      'Security Expense',
        Category.MISCELLANEOUS: 'Miscellaneous Expense',
    }

    class PaymentMethod(models.TextChoices):
        CASH          = 'cash',     'Cash'
        UPI           = 'upi',      'UPI'
        BANK_TRANSFER = 'bank',     'Bank Transfer'
        CREDIT_CARD   = 'credit',   'Credit Card'
        DEBIT_CARD    = 'debit',    'Debit Card'

    gym            = models.ForeignKey('Gym.Gym', on_delete=models.CASCADE,
                                       related_name='expenses', db_index=True)
    title          = models.CharField(max_length=150)
    category       = models.CharField(max_length=20, choices=Category.choices,
                                      default=Category.MISCELLANEOUS, db_index=True)
    amount         = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=10, choices=PaymentMethod.choices,
                                      default=PaymentMethod.CASH)
    expense_date   = models.DateField(default=timezone.localdate, db_index=True)
    note           = models.TextField(blank=True)
    receipt        = models.FileField(upload_to='expenses/receipts/%Y/%m/', null=True, blank=True)
    is_recurring   = models.BooleanField(default=False)
    template       = models.ForeignKey('ExpenseTemplate', on_delete=models.SET_NULL,
                                       null=True, blank=True, related_name='occurrences')

    created_by     = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='expenses_created')
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-expense_date', '-created_at']
        indexes = [
            models.Index(fields=['gym', 'expense_date']),
            models.Index(fields=['gym', 'category']),
        ]
        verbose_name = 'Expense'
        verbose_name_plural = 'Expenses'

    def __str__(self):
        return f"{self.title} — ₹{self.amount} ({self.get_category_display()})"

    @property
    def category_label(self):
        return self.get_category_display()

class ExpenseTemplate(models.Model):
    """
    Defines a recurring monthly expense (e.g. rent, staff salary).
    Does NOT store transaction data itself — each month's actual Expense
    row is generated from this template by services.generate_recurring_expenses().
    """

    gym            = models.ForeignKey('Gym.Gym', on_delete=models.CASCADE,
                                       related_name='expense_templates', db_index=True)
    title          = models.CharField(max_length=150)
    category       = models.CharField(max_length=20, choices=Expense.Category.choices,
                                      default=Expense.Category.MISCELLANEOUS)
    amount         = models.DecimalField(max_digits=10, decimal_places=2,
                                         help_text="Starting amount. Later occurrences use the "
                                                    "most recent non-deleted Expense's amount instead.")
    payment_method = models.CharField(max_length=10, choices=Expense.PaymentMethod.choices,
                                      default=Expense.PaymentMethod.CASH)
    note           = models.TextField(blank=True)

    is_active      = models.BooleanField(default=True, db_index=True)
    start_date     = models.DateField(help_text="First month this template generates an Expense for.")
    end_date       = models.DateField(null=True, blank=True,
                                      help_text="Optional. Template stops generating after this date.")

    next_run_date  = models.DateField(
        db_index=True,
        help_text="Next date this template is due to generate an Expense. "
                   "Advances automatically after each successful generation run.",
    )

    created_by     = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='expense_templates_created')
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['title']
        indexes = [
            models.Index(fields=['gym', 'is_active', 'next_run_date']),
        ]
        verbose_name = 'Recurring Expense Template'
        verbose_name_plural = 'Recurring Expense Templates'

    def __str__(self):
        status = 'active' if self.is_active else 'paused'
        return f"{self.title} — ₹{self.amount}/mo ({status})"