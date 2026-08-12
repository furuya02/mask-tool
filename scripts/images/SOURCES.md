# Test image sources

All test images live directly under `scripts/images/`.
`sample_*.jpg` are single photos from Wikimedia Commons; `neu_*.jpg` are from
the NEU-DET steel surface defect dataset (see the note below).

| File | Source | License | Attribution |
|---|---|---|---|
| sample_part_rust.jpg | [Technics RS-M270x rusty cassette holder frame](https://commons.wikimedia.org/wiki/File:Technics_RS-M270x_rusty_cassette_holder_frame.jpg) | CC0 | - |
| sample_rust.jpg | [Rusty steel plate](https://commons.wikimedia.org/wiki/File:Rusty_steel_plate.jpg) | CC0 | - |
| sample_scratch.jpg | [Dark grey stainless steel heavily scratched worn seamless metal surface texture](https://commons.wikimedia.org/wiki/File:Dark_grey_stainless_steel_heavily_scratched_worn_seamless_metal_surface_texture.jpg) | CC0 | - |
| sample_car_rust.jpg | [Car bodywork rusted through in BMW 318i E46](https://commons.wikimedia.org/wiki/File:025_Durchgerostet_Karosserie_-_car_bodywork_rusted_through_in_BMW_318i_E46.jpg) | CC BY 3.0 | (c) Marek Ślusarczyk (Tupungato), resized |
| sample_phone_crack.jpg | [MyPhone my27 front panel](https://commons.wikimedia.org/w/index.php?curid=196092174) | CC BY 4.0 | (c) JGBlue1509, resized. Used as a non-metal generality example (cracked screen) |
| neu_scratches_1.jpg | NEU-DET (scratches_1.jpg) | CC BY 4.0 | see NEU-DET note below |
| neu_scratches_2.jpg | NEU-DET (scratches_101.jpg) | CC BY 4.0 | see NEU-DET note below |
| neu_inclusion_1.jpg | NEU-DET (inclusion_100.jpg) | CC BY 4.0 | see NEU-DET note below |
| neu_patches_1.jpg | NEU-DET (patches_100.jpg) | CC BY 4.0 | see NEU-DET note below |

## NEU-DET note

The `neu_*.jpg` files are derived from the **NEU surface defect database
(NEU-DET)**, a hot-rolled steel strip surface defect dataset published by
Northeastern University (NEU).

- Original authors: K. Song, Y. Yan, Northeastern University (NEU), China
- Reference: K. Song and Y. Yan, "A noise robust method based on completed
  local binary patterns for hot-rolled steel strip surface defects,"
  Applied Surface Science, vol. 285, pp. 858-864, 2013.
- Redistribution used here: NEU-DET on Roboflow Universe (CC BY 4.0)
  https://universe.roboflow.com/neudatasetoriginal/neu-steel-defect-dataset

The original images are 200x200 grayscale. The `neu_*.jpg` files here are
upscaled 3x (to 600x600) with Lanczos resampling for readability and to give
the model more pixels to work with.
