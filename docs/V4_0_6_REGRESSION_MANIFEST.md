# V4.0.6 final MLS window-standard regression manifest

This manifest defines the bounded V4.0.6 visual acceptance gate. It does not
submit images or store customer photographs in the repository.

## Required production controls

| Control | Reference | Acceptance condition |
| --- | --- | --- |
| Strong exterior recovery | Chaterhouse 21 | Exterior remains immediately readable; a white, hazy, faint, or washed-out result fails. |
| Natural window control | Chaterhouse 18 | Bright interior, readable real foliage, natural geometry, and non-artificial light-blue sky remain intact. |
| Blue-visibility floor | Cherry Stone 10 and 19 | Visible daytime sky reads clearly light natural blue at normal viewing size; white, near-white, gray, or barely perceptible blue fails. |
| Reflection cleanup | Cherry Stone 10 | Photographer, camera, tripod, or phone is removed without changing mirror/room geometry. |

## Required offline checks

- Run compile and the complete mocked unit suite.
- Verify one authoritative prompt requires real-sky-first recovery, a clearly
  visible light-blue floor, a bounded realism ceiling, clipped-sky-only
  fallback, no dramatic clouds, covering-aware blue behavior, and no exterior
  invention.
- Verify Medium default; manual Low, Medium, High; manual-only High; and no
  OpenAI Auto.
- Verify one mocked direct `gpt-image-2` Images Edit request per normal image
  and local-only Premium Finish.

## Paid validation, only after explicit approval

Run only the four listed reference cases individually. Do not run a full
property until the strong exterior, light-blue visibility, natural realism, and
reflection-cleanup controls all pass together.
