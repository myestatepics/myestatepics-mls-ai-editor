# V4.0.5 fallback-sky realism regression manifest

This manifest isolates V4.0.5’s fallback-sky realism change. It does not
submit images or store customer photographs in the repository.

## Required production controls

| Control | Reference | Acceptance condition |
| --- | --- | --- |
| Strong window | Chaterhouse 21 | Must remain at least as readable and strong as V4.0.4; white, washed-out, hazy, or faint recovery fails. |
| Natural window | Chaterhouse 18 | Bright interior, readable genuine foliage, natural photographic appearance, and preserved window geometry remain intact. |
| Fallback-sky realism | Chaterhouse 8 | Exterior stays readable through blinds, while fallback blue is less prominent, pale, low contrast, and has minimal or no cloud definition. |

## Required offline checks

- Run compile and the complete mocked unit suite.
- Verify the single authoritative prompt retains mandatory exterior recovery,
  real-sky-first behavior, clipped-sky-only fallback, and exterior-invention
  prohibition.
- Verify fallback sky is pale, low saturation, low contrast, non-dramatic, and
  subject to extra restraint behind blinds, sheers, screens, or partial covers.
- Verify Medium remains the default; Low, Medium, and High remain available;
  High remains manual-only; Auto remains unavailable.
- Verify normal mocked processing still makes exactly one direct `gpt-image-2`
  Images Edit request and Premium Finish remains local.

## Paid validation, only after explicit approval

Run exactly the three controls above individually. Do not validate an entire
property until #21 remains strong, #18 remains natural, and #8 no longer looks
like an obvious sky replacement.
