package timeline

import (
	"cadenceforge/internal/domain"
	"cadenceforge/internal/timebase"
)

// Build places each span's source interval onto the cumulative output
// timeline. Placements stay in editorial milliseconds; nothing here snaps
// them to an encoding raster, because the frame and sample grids are not
// the plan's concern.
func Build(sheet domain.Sheet, recipe domain.Recipe, sources map[string]domain.Source) (domain.Plan, error) {
	if err := domain.ValidateSheet(sheet, sources); err != nil {
		return domain.Plan{}, err
	}
	if err := domain.ValidateRecipe(recipe); err != nil {
		return domain.Plan{}, err
	}
	plan := domain.Plan{Recipe: recipe, Placements: []domain.Placement{}}
	for _, span := range sheet.Spans {
		source := sources[span.SourceID]
		if recipe.Format == "mp4" && !source.Media.Video {
			return plan, domain.Invalid("MP4 span requires video: %s", source.ID)
		}
		if recipe.Format == "wav" && !source.Media.Audio {
			return plan, domain.Invalid("WAV span requires audio: %s", source.ID)
		}
		src := timebase.Interval{In: span.InMS, Out: span.OutMS}
		out := timebase.Interval{In: plan.DurationMS, Out: plan.DurationMS + src.Len()}
		plan.Placements = append(plan.Placements, domain.Placement{SourceID: source.ID, SourceInMS: src.In, SourceOutMS: src.Out, OutputInMS: out.In, OutputOutMS: out.Out, GainDB: span.GainDB})
		plan.DurationMS = out.Out
	}
	return plan, nil
}
