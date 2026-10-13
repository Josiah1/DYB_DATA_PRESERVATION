#include <TFile.h>
#include <TLeaf.h>
#include <TTree.h>
#include <cassert>
#include <cstring>
#include <string>
#include <vector>

void add_muon(const char* path, const char* fault = "") {
  TFile file(path, "UPDATE");
  auto* base = file.Get<TTree>("Event/Rec/AdSimpleNL");
  file.cd("Event/Rec");
  base->SetBranchStatus("unusedBranch", false);
  Float_t energy = 0, x = 0;
  base->SetBranchAddress("energy", &energy);
  base->SetBranchAddress("x", &x);
  auto* simple = base->CloneTree(0);
  simple->SetName("AdSimple");
  auto* scaled = base->CloneTree(0);
  scaled->SetName("AdScaled");
  auto* arm = base->CloneTree(0);
  arm->SetName("ARMuon");
  Float_t values[12] = {};
  Double_t wrong_type = 0;
  Int_t status[3] = {};
  const std::vector<std::string> floats = {
      "rawEvis", "t", "dx", "dy", "dz", "dt",
      "energyQuality", "positionQuality", "directionQuality"};
  const std::vector<std::string> ints = {"energyStatus", "positionStatus", "directionStatus"};
  for (std::size_t i = 0; i < floats.size(); ++i)
    if (std::string(fault) == "type" && floats[i] == "dt")
      arm->Branch("dt", &wrong_type, "dt/D");
    else if (!(std::string(fault) == "missing" && floats[i] == "dt"))
      arm->Branch(floats[i].c_str(), &values[i], (floats[i] + "/F").c_str());
  for (std::size_t i = 0; i < ints.size(); ++i)
    arm->Branch(ints[i].c_str(), &status[i], (ints[i] + "/I").c_str());
  for (Long64_t e = 0; e < base->GetEntries(); ++e) {
    base->GetEntry(e);
    energy = 21.f + e; x += 1000;
    simple->Fill();
    if (std::string(fault) == "identity" && e == 0) base->GetEntry(1);
    scaled->Fill();
  }
  if (std::string(fault) != "empty") {
    // Deliberately sparse and reversed: ARMuon is not an entry-aligned friend.
    for (Long64_t e = base->GetEntries(); e-- > 0;) {
      base->GetEntry(e);
      if (base->GetLeaf("detector")->GetValue() < 5) continue;
      for (std::size_t i = 0; i < floats.size(); ++i) values[i] = 10.f * e + 0.25f * i;
      for (std::size_t i = 0; i < ints.size(); ++i) status[i] = (e + i) % 4;
      arm->Fill();
    }
  }
  if (std::string(fault) == "count") scaled->SetEntries(scaled->GetEntries() - 1);
  simple->Write(); scaled->Write(); arm->Write();
  file.Close();
}

void compare_muon(const char* result, const char* first, const char* second) {
  TFile output(result);
  const std::vector<const char*> names = {"AdSimple", "AdScaled", "ARMuon"};
  const std::vector<const char*> paths = {first, second};
  for (auto* name : names) {
    auto* out = output.Get<TTree>((std::string("Event/Rec/") + name).c_str());
    assert(out);
    Long64_t offset = 0;
    for (std::size_t s = 0; s < paths.size(); ++s) {
      TFile input(paths[s]);
      auto* in = input.Get<TTree>((std::string("Event/Rec/") + name).c_str());
      for (Long64_t e = 0; e < in->GetEntries(); ++e) {
        assert(in->GetEntry(e) > 0 && out->GetEntry(offset + e) > 0);
        assert(out->GetLeaf("runNumber")->GetValueLong64() == 7777);
        assert(out->GetLeaf("sourceIndex")->GetValueLong64() == static_cast<Long64_t>(s));
        assert(out->GetLeaf("sourceEntry")->GetValueLong64() == e);
        for (auto* object : *out->GetListOfLeaves()) {
          auto* leaf = static_cast<TLeaf*>(object);
          const std::string field = leaf->GetName();
          if (field == "runNumber" || field == "sourceIndex" || field == "sourceEntry") continue;
          auto* original = in->GetLeaf(field.c_str());
          assert(original && std::string(original->GetTypeName()) == leaf->GetTypeName());
          assert(std::memcmp(original->GetValuePointer(), leaf->GetValuePointer(),
                             leaf->GetLenType()) == 0);
        }
      }
      offset += in->GetEntries();
    }
    assert(out->GetEntries() == offset);
  }
}

void corrupt_muon_provenance(const char* path) {
  TFile file(path, "UPDATE");
  auto* sources = file.Get<TTree>("Preservation/Sources");
  Long64_t counts[3]{};
  sources->SetBranchAddress("muonEntries", counts);
  file.cd("Preservation");
  auto* altered = sources->CloneTree(0);
  for (Long64_t e = 0; e < sources->GetEntries(); ++e) {
    sources->GetEntry(e);
    counts[2] += 1;
    altered->Fill();
  }
  altered->Write("Sources", TObject::kOverwrite);
}
