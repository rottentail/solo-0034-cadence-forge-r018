package service

import (
	"cadenceforge/internal/caption"
	"cadenceforge/internal/domain"
	"cadenceforge/internal/timeline"
)

// compose assembles the plan a preview shows and a submit freezes: the
// timeline placements plus the caption track mapped onto them. Sheets and
// recipes revise independently, but both entry points share this one
// orchestration, so a frozen render and a later preview of the same
// revisions observe identical time relationships.
func compose(sheet domain.Sheet, recipe domain.Recipe, sources map[string]domain.Source) (domain.Plan, error) {
	plan, err := timeline.Build(sheet, recipe, sources)
	if err != nil {
		return plan, err
	}
	plan.VTT = caption.VTT(caption.Map(sheet, plan))
	return plan, nil
}
