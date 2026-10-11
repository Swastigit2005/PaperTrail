# Example documents (synthetic)

Every file here is synthetic: fake people, companies, GSTINs and amounts. Drag a pair into the PaperTrail demo's
upload slots (or use "Use pair" in the demo). Because these exact files are bundled with the demo, their
extraction is precomputed with the live engine: they work instantly and never use model quota.


## claims

1. **Clean claim (brand vs generic names)**: seeded issue: nothing (should approve).
   - `claims/claims-01-clean-prescription.jpg` (prescription)
   - `claims/claims-01-clean-bill.jpg` (bill)
2. **Bill includes a medicine that was not prescribed**: seeded issue: unprescribed medicine.
   - `claims/claims-02-unprescribed-prescription.jpg` (prescription)
   - `claims/claims-02-unprescribed-bill.jpg` (bill)
3. **Billed quantity far exceeds the prescribed course**: seeded issue: implausible quantity, bill before prescription.
   - `claims/claims-03-quantity-prescription.jpg` (prescription)
   - `claims/claims-03-quantity-bill.jpg` (bill)
4. **Bill is for a different patient**: seeded issue: patient name mismatch.
   - `claims/claims-04-patient-prescription.jpg` (prescription)
   - `claims/claims-04-patient-bill.jpg` (bill)
5. **Bill totals do not add up**: seeded issue: arithmetic error.
   - `claims/claims-05-arithmetic-prescription.jpg` (prescription)
   - `claims/claims-05-arithmetic-bill.jpg` (bill)

## expenses

1. **Clean meal claim**: seeded issue: nothing (should approve).
   - `expenses/expenses-01-clean-receipt.jpg` (receipt)
   - `expenses/expenses-01-clean-expense_claim.jpg` (expense claim)
2. **Claimed amount exceeds the receipt**: seeded issue: claim amount mismatch.
   - `expenses/expenses-02-inflated-receipt.jpg` (receipt)
   - `expenses/expenses-02-inflated-expense_claim.jpg` (expense claim)
3. **Same receipt claimed again (duplicate)**: seeded issue: duplicate receipt.
   - `expenses/expenses-03-duplicate-receipt.jpg` (receipt)
   - `expenses/expenses-03-duplicate-expense_claim.jpg` (expense claim)
4. **Receipt older than the 30-day policy window**: seeded issue: outside policy window.
   - `expenses/expenses-04-late-receipt.jpg` (receipt)
   - `expenses/expenses-04-late-expense_claim.jpg` (expense claim)
5. **Clean team-lunch claim (phone scan)**: seeded issue: nothing (should approve).
   - `expenses/expenses-05-clean_2-receipt.jpg` (receipt)
   - `expenses/expenses-05-clean_2-expense_claim.jpg` (expense claim)

## payables

1. **Clean invoice (paraphrased lines)**: seeded issue: nothing (should approve).
   - `payables/payables-01-clean-invoice.jpg` (invoice)
   - `payables/payables-01-clean-purchase_order.jpg` (purchase order)
2. **Rate above PO and quantity over PO**: seeded issue: rate above po, qty over po.
   - `payables/payables-02-overbilled-invoice.jpg` (invoice)
   - `payables/payables-02-overbilled-purchase_order.jpg` (purchase order)
3. **Wrong tax split and invalid GSTIN**: seeded issue: wrong tax split, invalid gstin.
   - `payables/payables-03-tax-invoice.jpg` (invoice)
   - `payables/payables-03-tax-purchase_order.jpg` (purchase order)
4. **Invoice bills an item that was never ordered**: seeded issue: line not on po.
   - `payables/payables-04-extra_line-invoice.jpg` (invoice)
   - `payables/payables-04-extra_line-purchase_order.jpg` (purchase order)
5. **Different vendor GSTIN and a wrong grand total**: seeded issue: gstin mismatch, total mismatch.
   - `payables/payables-05-vendor-invoice.jpg` (invoice)
   - `payables/payables-05-vendor-purchase_order.jpg` (purchase order)
