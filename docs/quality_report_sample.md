# Data Quality Report

91 findings in the staged inventory sheet.

## [5] Rows with no DTF Part # (can't be linked to a material):

```
source rows: [4, 34, 36, 62, 88]
```

## [6] DTF Part #s reused across different material names:

```
FL-7126: ['green apple flavor art', 'methylcobalamin (b12) 1%']  (source rows: [3, 65, 67])
FL-6310: ['lemonade flavor nat', 'orange cream flavor n&a']  (source rows: [7, 74, 79, 91])
RM-1750: ['malic acid', 'methylcobalamin (b12) 1%']  (source rows: [14, 41, 58, 102])
RM-7201: ['l-carnitine tartrate', 'niacinamide']  (source rows: [17, 32, 73])
RM-6881: ['ashwagandha extract ksm-66', 'creatine monohydrate']  (source rows: [20, 53, 72, 81])
RM-7227: ['bitter blocker bb-40', 'sodium citrate']  (source rows: [49, 78, 83])
```

## [22] Part #s listed under more than one supplier (only the first is kept on the material row):

```
FL-2876: ['sensapure flavors', 'sensapure flavors inc']
RM-7572: ['blue california', 'blue california inc.']
FL-7126: ['glanbia nutr.', 'nutrasci llc']
RM-3340: ['flavor sum', 'flavorsum llc']
FL-6310: ['nutrasci llc', 'sensapure', 'sensapure flavors', 'sensapure flavors inc']
FL-6573: ['ingredion', 'ingredion inc']
FL-2040: ['anderson', 'anderson advanced ingredients']
RM-4611: ['anderson adv ing', 'anderson advanced ingredients']
RM-1750: ['flavorsum llc', 'nutra sci', 'nutrasci']
RM-7201: ['nutra sci', 'nutrasci', 'prinova usa']
```

## [4] Locations not matching the expected code format (e.g. 6L-27-D) or a known named location:

```
'BACK COOLER'  x3  (rows [29, 37, 97])
'3R-24-'  x1  (rows [64])
-> a repeated value here is usually a real named location to whitelist;
   a one-off is usually a typo.
```

## [8] Rows with no location.

## [13] Supplier spellings that look like the same company:

```
'Anderson'  ~  'Anderson Adv Ing'   (similarity 0.67)
'Anderson Adv Ing'  ~  'Anderson Advanced Ingredients'   (similarity 0.71)
'Blue California'  ~  'Blue California Inc.'   (similarity 0.86)
'Flavor Sum'  ~  'Flavorsum LLC'   (similarity 0.78)
'Glanbia'  ~  'Glanbia Nutr.'   (similarity 0.7)
'Glanbia Nutr.'  ~  'Glanbia Nutritionals'   (similarity 0.73)
'Ingredion'  ~  'Ingredion Inc'   (similarity 0.82)
'Nutra Sci'  ~  'NutraSci'   (similarity 0.94)
'Nutra Sci'  ~  'NutraSci LLC'   (similarity 0.76)
'NutraSci'  ~  'NutraSci LLC'   (similarity 0.8)
'PRINOVA'  ~  'Prinova USA'   (similarity 0.78)
'SensaPure Flavors Inc'  ~  'Sensapure Flavors'   (similarity 0.89)
'Sensapure'  ~  'Sensapure Flavors'   (similarity 0.69)
```

## [5] Prices that couldn't be parsed as numbers:

```
row 12: 'TBD'
row 40: 'TBD'
row 88: 'call'
row 97: 'call'
row 101: 'call'
```

## [11] Rows with a blank price.

## [10] Rows with a blank receiving date.

## [7] Rows with a blank category.

