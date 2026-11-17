def boundary_terms(mesh):
    return {edge: occurrences[0][1]
            for edge, occurrences in sorted(mesh.incidence.items())
            if len(occurrences) == 1}


def internal_terms(mesh):
    return {edge: sum(sign for _, sign in occurrences)
            for edge, occurrences in sorted(mesh.incidence.items())
            if len(occurrences) == 2}


def summed_face_chain(mesh):
    chain = boundary_terms(mesh)
    chain.update(internal_terms(mesh))
    return dict(sorted(chain.items()))
