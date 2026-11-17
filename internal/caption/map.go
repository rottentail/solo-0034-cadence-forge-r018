package caption

import (
	"cadenceforge/internal/domain"
	"cadenceforge/internal/timebase"
	"fmt"
	"html"
	"sort"
	"strings"
)

// Cue is a source cue clipped and shifted onto the output timeline.
type Cue struct {
	StartMS, EndMS int64
	Text           string
	Order          int
}

// Map intersects each source cue with every placement of its source and
// shifts the overlap onto the output timeline; a source repeated across
// spans maps the same cue once per occurrence.
func Map(sheet domain.Sheet, plan domain.Plan) []Cue {
	out := []Cue{}
	for _, p := range plan.Placements {
		src := timebase.Interval{In: p.SourceInMS, Out: p.SourceOutMS}
		delta := p.OutputInMS - p.SourceInMS
		for i, c := range sheet.Cues {
			if c.SourceID != p.SourceID {
				continue
			}
			clip, ok := src.Intersect(timebase.Interval{In: c.StartMS, Out: c.EndMS})
			if !ok {
				continue
			}
			mapped := clip.Shift(delta)
			out = append(out, Cue{StartMS: mapped.In, EndMS: mapped.Out, Text: c.Text, Order: i})
		}
	}
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].StartMS != out[j].StartMS {
			return out[i].StartMS < out[j].StartMS
		}
		return out[i].Order < out[j].Order
	})
	return out
}

// VTT renders mapped cues as a WebVTT document; timestamps use the shared
// millisecond clock notation.
func VTT(cues []Cue) string {
	var b strings.Builder
	b.WriteString("WEBVTT\n\n")
	for i, c := range cues {
		fmt.Fprintf(&b, "%d\n%s --> %s\n%s\n\n", i+1, timebase.Stamp(c.StartMS), timebase.Stamp(c.EndMS), strings.ReplaceAll(html.EscapeString(c.Text), "\n\n", "\n"))
	}
	return b.String()
}
