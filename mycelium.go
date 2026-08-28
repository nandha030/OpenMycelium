package main

import (
	"fmt"
	"math"
)

const (
	myceliumAlgorithmName    = "Mycelium"
	myceliumAlgorithmVersion = "1.0.0"
)

// MyceliumDecision records an explainable step in the heterogeneous optimizer.
type MyceliumDecision struct {
	Stage    string `json:"stage"`
	Decision string `json:"decision"`
	Reason   string `json:"reason"`
}

// MyceliumOptimization is persisted with every execution plan so a placement
// can be audited and reproduced instead of remaining a scheduler black box.
type MyceliumOptimization struct {
	Algorithm           string             `json:"algorithm"`
	Version             string             `json:"version"`
	Objective           string             `json:"objective"`
	CandidatesEvaluated int                `json:"candidatesEvaluated"`
	SelectedCandidate   string             `json:"selectedCandidate"`
	Score               float64            `json:"score"`
	ProfileCoverage     float64            `json:"profileCoverage"`
	StageImbalance      float64            `json:"stageImbalance"`
	MemoryFeasible      bool               `json:"memoryFeasible"`
	Decisions           []MyceliumDecision `json:"decisions"`
}

type myceliumCandidate struct {
	contract ParallelContract
	name     string
	penalty  float64
	score    float64
}

func myceliumTransportPenalty(transport string, heterogeneous bool) float64 {
	if !heterogeneous {
		return 0.08
	}
	switch transport {
	case "gloo", "cpu-forwarding":
		return 0.35
	case "mccl-tcp":
		return 0.32
	case "device-direct":
		return 0.12
	case "mccl":
		return 0.10
	default:
		return 0.40
	}
}

func myceliumLayerCapacity(group ExecutionGroup, modelStateGiB float64, layers int) int {
	if group.MemoryGiB <= 0 || modelStateGiB <= 0 || layers <= 0 {
		return math.MaxInt
	}
	perLayerGiB := modelStateGiB / float64(layers)
	if perLayerGiB <= 0 {
		return math.MaxInt
	}
	return int(math.Floor(group.MemoryGiB * float64(group.Devices) / perLayerGiB))
}

// allocateMyceliumLayers greedily minimizes the slowest predicted pipeline
// stage while observing profiled aggregate memory where measurements exist.
func allocateMyceliumLayers(groups []ExecutionGroup, totalLayers int, modelStateGiB float64) ([]ExecutionGroup, bool) {
	allocated := append([]ExecutionGroup(nil), groups...)
	capacities := make([]int, len(allocated))
	for index := range allocated {
		capacities[index] = myceliumLayerCapacity(allocated[index], modelStateGiB, totalLayers)
	}
	memoryFeasible := true
	for layer := 0; layer < totalLayers; layer++ {
		best, bestTime := -1, math.MaxFloat64
		for index := range allocated {
			if allocated[index].Layers >= capacities[index] {
				continue
			}
			capacity := allocated[index].ThroughputWeight * float64(allocated[index].Devices)
			if capacity <= 0 {
				capacity = 1
			}
			projected := float64(allocated[index].Layers+1) / capacity
			if projected < bestTime {
				best, bestTime = index, projected
			}
		}
		if best < 0 {
			memoryFeasible = false
			for index := range allocated {
				capacity := allocated[index].ThroughputWeight * float64(allocated[index].Devices)
				if capacity <= 0 {
					capacity = 1
				}
				projected := float64(allocated[index].Layers+1) / capacity
				if projected < bestTime {
					best, bestTime = index, projected
				}
			}
		}
		allocated[best].Layers++
	}
	return allocated, memoryFeasible
}

func myceliumStageImbalance(groups []ExecutionGroup) float64 {
	if len(groups) < 2 {
		return 0
	}
	minimum, maximum := math.MaxFloat64, 0.0
	for _, group := range groups {
		capacity := group.ThroughputWeight * float64(group.Devices)
		if capacity <= 0 {
			capacity = 1
		}
		stageTime := float64(group.Layers) / capacity
		if stageTime < minimum {
			minimum = stageTime
		}
		if stageTime > maximum {
			maximum = stageTime
		}
	}
	if maximum == 0 || minimum == math.MaxFloat64 {
		return 0
	}
	return (maximum - minimum) / maximum
}

func myceliumCandidates(request ExecutionPlanRequest, groups []ExecutionGroup, transport string, deviceCount, vendorCount int) []myceliumCandidate {
	heterogeneous := vendorCount > 1
	basePenalty := myceliumTransportPenalty(transport, heterogeneous)
	if !heterogeneous {
		mode := "data-parallel"
		if request.ZeroStage > 0 {
			mode = "zero-data-parallel"
		}
		return []myceliumCandidate{{name: mode, penalty: basePenalty, contract: ParallelContract{Pipeline: 1, Tensor: 1, Data: deviceCount, Expert: 1, Zero: request.ZeroStage, Mode: mode}}}
	}
	pipeline := len(groups)
	if pipeline > request.MaxPipelineStages {
		pipeline = request.MaxPipelineStages
	}
	candidates := []myceliumCandidate{{
		name: "heterogeneous-pipeline", penalty: basePenalty,
		contract: ParallelContract{Pipeline: pipeline, Tensor: 1, Data: int(math.Max(1, float64(deviceCount/pipeline))), Expert: 1, Zero: request.ZeroStage, Mode: "heterogeneous-pipeline"},
	}}
	if transport == "mccl" || transport == "mccl-tcp" || transport == "device-direct" {
		mode := "heterogeneous-data"
		if request.ZeroStage > 0 {
			mode = "heterogeneous-zero"
		}
		candidates = append(candidates, myceliumCandidate{
			name: mode, penalty: math.Max(0.04, basePenalty-0.02),
			contract: ParallelContract{Pipeline: 1, Tensor: 1, Data: deviceCount, Expert: 1, Zero: request.ZeroStage, Mode: mode},
		})
	}
	return candidates
}

func selectMyceliumCandidate(request ExecutionPlanRequest, candidates []myceliumCandidate, throughput, imbalance, powerWatts float64) myceliumCandidate {
	best := candidates[0]
	best.score = -1
	for index := range candidates {
		candidate := &candidates[index]
		candidate.score = throughput * (1 - candidate.penalty)
		if candidate.contract.Pipeline > 1 {
			candidate.score *= 1 - math.Min(0.60, imbalance*0.50)
		}
		switch request.Objective {
		case "balanced":
			candidate.score *= 1 - math.Min(0.50, imbalance*0.75)
		case "efficiency":
			if powerWatts > 0 {
				candidate.score = candidate.score * 1000 / powerWatts
			}
		}
		forced := (request.Strategy == "pipeline" && candidate.contract.Pipeline > 1) ||
			(request.Strategy == "data" && candidate.contract.Mode == "heterogeneous-data") ||
			(request.Strategy == "zero" && candidate.contract.Zero > 0 && candidate.contract.Pipeline == 1)
		if request.Strategy != "auto" && !forced {
			continue
		}
		if candidate.score > best.score {
			best = *candidate
		}
	}
	if best.score < 0 {
		best = candidates[0]
		best.score = throughput * (1 - best.penalty)
	}
	return best
}

func compileMyceliumOptimization(request ExecutionPlanRequest, groups []ExecutionGroup, transport string, modelStateGiB float64) ([]ExecutionGroup, ParallelContract, ExecutionEstimate, MyceliumOptimization, []string) {
	optimized, memoryFeasible := allocateMyceliumLayers(groups, request.Layers, modelStateGiB)
	weights := make([]float64, len(optimized))
	profiled, totalThroughput, powerWatts := 0, 0.0, 0.0
	vendors, deviceCount := map[string]bool{}, 0
	for index, group := range optimized {
		weights[index] = group.ThroughputWeight * float64(group.Devices)
		if group.ProfileSource != "conservative-default" {
			profiled++
			totalThroughput += weights[index]
		}
		powerWatts += group.PowerWatts * float64(group.Devices)
		vendors[group.Vendor] = true
		deviceCount += group.Devices
	}
	batchWeights := append([]float64(nil), weights...)
	if !request.DynamicMicroBatch {
		for index := range batchWeights {
			batchWeights[index] = 1
		}
	}
	batches := distributeInteger(request.GlobalBatch, batchWeights)
	for index := range optimized {
		optimized[index].MicroBatch = batches[index]
	}
	imbalance := myceliumStageImbalance(optimized)
	candidates := myceliumCandidates(request, optimized, transport, deviceCount, len(vendors))
	selected := selectMyceliumCandidate(request, candidates, totalThroughput, imbalance, powerWatts)
	predicted := totalThroughput * (1 - selected.penalty)
	if selected.contract.Pipeline > 1 {
		predicted *= 1 - math.Min(0.60, imbalance*0.50)
	}
	iteration := 0.0
	if predicted > 0 {
		iteration = float64(request.SequenceLength*request.GlobalBatch) / predicted * 1000
	}
	coverage := float64(profiled) / math.Max(1, float64(len(optimized)))
	confidence := "estimated"
	if coverage == 1 {
		confidence = "profiled"
	} else if coverage > 0 {
		confidence = "partial"
	}
	warnings := []string{}
	if !memoryFeasible {
		warnings = append(warnings, "Mycelium could not satisfy all profiled memory limits; the plan requires a larger ZeRO stage, activation checkpointing, offload, or additional devices")
	}
	optimization := MyceliumOptimization{
		Algorithm: myceliumAlgorithmName, Version: myceliumAlgorithmVersion, Objective: request.Objective,
		CandidatesEvaluated: len(candidates), SelectedCandidate: selected.name,
		Score: math.Round(selected.score*100) / 100, ProfileCoverage: math.Round(coverage*1000) / 1000,
		StageImbalance: math.Round(imbalance*1000) / 1000, MemoryFeasible: memoryFeasible,
		Decisions: []MyceliumDecision{
			{Stage: "inventory", Decision: fmt.Sprintf("selected %d devices in %d accelerator groups", deviceCount, len(optimized)), Reason: "only ready, schedulable, filtered capacity is considered"},
			{Stage: "placement", Decision: fmt.Sprintf("balanced %d layers with %.1f%% predicted stage imbalance", request.Layers, imbalance*100), Reason: "greedy minimax allocation uses measured throughput and aggregate memory constraints"},
			{Stage: "parallelism", Decision: selected.name, Reason: fmt.Sprintf("highest %s score across %d admissible candidates", request.Objective, len(candidates))},
			{Stage: "transport", Decision: transport, Reason: fmt.Sprintf("estimated communication penalty %.1f%%", selected.penalty*100)},
		},
	}
	estimate := ExecutionEstimate{
		ModelStateGiB:          roundGiB(modelStateGiB),
		MinimumDeviceMemoryGiB: roundGiB(modelStateGiB / math.Max(1, float64(deviceCount))),
		PredictedTokensSecond:  math.Round(predicted*100) / 100,
		PredictedIterationMS:   math.Round(iteration*100) / 100,
		CommunicationPenalty:   selected.penalty,
		Confidence:             confidence,
	}
	return optimized, selected.contract, estimate, optimization, warnings
}
