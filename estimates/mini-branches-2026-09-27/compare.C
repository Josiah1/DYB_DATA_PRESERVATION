// One-file, selection-on pilot: compare every retained input value to the slim output.
// Run with ROOT: root -l -b -q 'compare.C("input.root","slim.root",82379)'
#include <TFile.h>
#include <TLeaf.h>
#include <TTree.h>

#include <cmath>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

void compare(const char* input_path, const char* output_path, int run_number) {
  TFile input(input_path, "READ"), output(output_path, "READ");
  if (input.IsZombie() || output.IsZombie()) throw std::runtime_error("cannot open pilot files");
  const char* paths[] = {"Event/Rec/AdSimpleNL", "Event/Data/CalibStats"};
  const std::vector<std::string> fields[] = {
      {"site", "detector", "triggerType", "triggerNumber", "triggerTimeSec",
       "triggerTimeNanoSec", "energy", "x", "y", "z"},
      {"nHit", "MiddleTimeRMS", "Quadrant", "Q1", "Q2", "MaxQ", "MaxQ_2inchPMT",
       "Kurtosis", "time_PSD_local_RMS"}};
  TTree* before[2];
  TTree* after[2];
  for (int tree = 0; tree < 2; ++tree) {
    before[tree] = input.Get<TTree>(paths[tree]);
    after[tree] = output.Get<TTree>(paths[tree]);
    if (!before[tree] || !after[tree]) throw std::runtime_error("missing event tree");
    before[tree]->SetBranchStatus("*", false);
    after[tree]->SetBranchStatus("*", false);
    for (const auto& field : fields[tree]) {
      auto* first = before[tree]->GetLeaf(field.c_str());
      auto* second = after[tree]->GetLeaf(field.c_str());
      if (!first || !second || first->GetLen() != 1 || second->GetLen() != 1 ||
          std::string(first->GetTypeName()) != second->GetTypeName())
        throw std::runtime_error("missing, nonscalar or differently typed leaf: " + field);
      before[tree]->SetBranchStatus(field.c_str(), true);
      after[tree]->SetBranchStatus(field.c_str(), true);
    }
  }
  if (before[0]->GetEntries() <= 0 || before[0]->GetEntries() != before[1]->GetEntries() ||
      after[0]->GetEntries() != after[1]->GetEntries() || !after[0]->GetLeaf("runNumber"))
    throw std::runtime_error("invalid counts or missing run provenance");
  after[0]->SetBranchStatus("runNumber", true);
  Long64_t kept = 0;
  for (Long64_t entry = 0; entry < before[0]->GetEntries(); ++entry) {
    if (before[0]->GetEntry(entry) <= 0) throw std::runtime_error("cannot read input AD entry");
    const double detector = before[0]->GetLeaf("detector")->GetValue();
    const double energy = before[0]->GetLeaf("energy")->GetValue();
    if (detector >= 1 && detector <= 4 && !(energy > 0.7)) continue;
    if (before[1]->GetEntry(entry) <= 0 || after[0]->GetEntry(kept) <= 0 ||
        after[1]->GetEntry(kept) <= 0)
      throw std::runtime_error("cannot read retained entry");
    for (int tree = 0; tree < 2; ++tree) {
      for (const auto& field : fields[tree]) {
        const double expected = before[tree]->GetLeaf(field.c_str())->GetValue();
        const double actual = after[tree]->GetLeaf(field.c_str())->GetValue();
        if (expected != actual && !(std::isnan(expected) && std::isnan(actual)))
          throw std::runtime_error("value mismatch at input entry " + std::to_string(entry) +
                                   ", leaf " + field);
      }
    }
    if (after[0]->GetLeaf("runNumber")->GetValue() != run_number)
      throw std::runtime_error("wrong run provenance");
    ++kept;
  }
  if (kept != after[0]->GetEntries()) throw std::runtime_error("extra output entries");
  std::cout << "COMPARE source_entries=" << before[0]->GetEntries() << " kept_entries=" << kept
            << " matched_source_branches=19 run=" << run_number
            << " nan_comparison=both_nan\n";
}
