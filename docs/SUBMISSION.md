# Field Shift: Space Apps 2026 project page text

## High-level summary

Field Shift helps a farmer decide whether a different crop rotation would suit their own field better. From a map point, the current rotation, water access and priorities, it uses NASA POWER climate to compute FAO-56 crop water use and compares the farmer's rotation with the rotations actually practised in that region. Each rotation gets transparent scores for water, soil-health practices and heat-stress exposure, a month-by-month plan and a plain explanation of what was and was not assessed. MODIS NDVI and, in the US, the USDA Cropland Data Layer check the result against what the satellite and the field history show.

## Project Details

### What does it do and how does it work?

The farmer clicks a field and enters a rotation, water access and priorities, optionally with a field area and a warming or water-cut scenario. Field Shift fetches 35 years of NASA POWER monthly data for that point, computes reference evapotranspiration with FAO-56 Penman-Monteith, and applies crop coefficients month by month to get irrigation need, or the share of crop water demand that rain leaves unmet where farming is rainfed. It scores every local rotation, ranks them with the farmer's weights, shows how the winner changes under other weightings, and writes a year plan and an explanation. Near Tanta in the Nile Delta, the best alternative to rice then wheat is maize then faba bean: it needs 24% less irrigation water, 3,781 cubic metres a year on a 2-feddan field. Where prices exist, a trade-off is shown without being scored: maize then wheat saves 17.5% of the water, but its indicative gross revenue is about 42% lower (February 2024 procurement prices, national average yields, no costs). Near Ames, Iowa, the farmer does not need to type anything: the Cropland Data Layer shows soybeans and corn alternating from 2020 to 2025, so the tool detects maize then soybean with high confidence. MODIS NDVI shows two greenness seasons a year at Tanta (peaks in February and July) and one in Iowa (July), matching the rotations.

### What benefits does it have?

Every score shows the numbers behind it, so a farmer or extension officer can check it. The tool compares known local rotations instead of inventing new ones, labels provisional values as provisional, and says plainly what it does not assess: costs, pests, soil tests. It runs offline from cached data.

### What is the intended impact?

We want water and climate questions to become part of everyday rotation choices, starting with the Nile Delta, where irrigation water is scarce. The tool reports water savings and score changes for one field; it does not predict yields, profit or adoption.

### What tools, coding languages, hardware or software did you use?

Python with FastAPI, Pydantic, NumPy, SciPy, pandas and pyproj; pytest (203 offline tests). The React frontend is planned, not yet built. The optional chat uses the OpenAI or Anthropic API. No special hardware: a laptop.

### How is your project creative?

It combines NASA climate with satellite greenness and US field history, so the tool can both compute and cross-check a rotation. Rainfed farms are scored on the share of crop water demand that rain covers, not on raw millimetres. The optional farmer chat is grounded: it answers only from the analysis, flags any number not in it, and refuses pesticide advice.

### What factors did your team consider?

Honesty about uncertainty: grid-cell climate, a POWER rainfall discontinuity we warn about, crop coefficients and calendars still awaiting verification. We also considered offline use, Arabic for Egyptian farmers, and keeping revenue unscored because costs are unknown.

## NASA Data

- NASA POWER (Prediction Of Worldwide Energy Resources), monthly point API: https://power.larc.nasa.gov/api/temporal/monthly/point (project site https://power.larc.nasa.gov/, methodology https://power.larc.nasa.gov/docs/methodology/)
- MODIS MOD13Q1 Terra Vegetation Indices 16-Day 250 m, via the ORNL DAAC TESViS web service: https://modis.ornl.gov/rst/api/v1/ (documentation https://modis.ornl.gov/data/modis_webservice.html)

## Space Agency Partner & Other Data

No partner space agency data was used. Other data:

- USDA NASS Cropland Data Layer via CropScape: https://nassgeodata.gmu.edu/axis2/services/CDLService/GetCDLValue (legend https://www.nass.usda.gov/sites/default/files/Research_and_Science/Cropland/docs/cdl_codes_names.xlsx)
- ISRIC SoilGrids v2.0: https://rest.isric.org/soilgrids/v2.0/properties/query
- FAO ECOCROP: https://gaez.fao.org/pages/ecocrop, via https://github.com/OpenCLIM/ecocrop/blob/main/EcoCrop_DB.csv
- FAO Irrigation and Drainage Paper 56: https://www.fao.org/4/x0490e/x0490e0b.htm
- USDA FAS GAIN reports EG2024-0011, EG2024-0023 and EG2024-0008 (see References)

## References

- Allen et al. 1998, FAO Irrigation and Drainage Paper 56, Crop Evapotranspiration. https://www.fao.org/4/x0490e/x0490e0b.htm
- Brown, M. J., Redhead, J., Pywell, R., Price, J. EcoCrop Climatic Crop Suitability Scoring. doi:10.5281/zenodo.10843625
- ISRIC SoilGrids v2.0. https://rest.isric.org/soilgrids/v2.0/properties/query
- ORNL DAAC. 2018. Terrestrial Ecology Subsetting & Visualization Services (TESViS) RESTful Web Service. ORNL DAAC, Oak Ridge, Tennessee, USA. https://doi.org/10.3334/ORNLDAAC/1600
- USDA FAS GAIN EG2024-0011, Sugar Annual, Cairo: https://apps.fas.usda.gov/newgainapi/api/Report/DownloadReportByFileName?fileName=Sugar+Annual_Cairo_Egypt_EG2024-0011.pdf
- USDA FAS GAIN EG2024-0023, Grain and Feed Update, Cairo: https://apps.fas.usda.gov/newgainapi/api/Report/DownloadReportByFileName?fileName=Grain+and+Feed+Update_Cairo_Egypt_EG2024-0023
- USDA FAS GAIN EG2024-0008, Oilseeds and Products Annual, Cairo (third-party mirror): https://www.oilworld.ru/data/postfiles/347868/Oilseeds_and_Products_Egypt_2024-0008.pdf

## Use of AI (SI)

Code was generated and refactored with Claude (Anthropic) via Claude Code, and Claude chat supported research and planning; the team chose the problem, data, methods and agronomic rules, and reviewed and tested all AI output. At runtime, the optional farmer chat calls an LLM (OpenAI gpt-6-luna by default, or Anthropic Claude) only to phrase answers from the engine's own results; it computes nothing that is scored. The next-year climate projection is not AI: it is a least-squares statistical model fitted by our code to NASA POWER data. Full disclosure: AI_USAGE.md.
